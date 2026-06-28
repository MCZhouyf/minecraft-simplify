package mcdrift;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.block.Block;
import net.minecraft.block.BlockState;
import net.minecraft.block.Blocks;
import net.minecraft.block.entity.AbstractFurnaceBlockEntity;
import net.minecraft.entity.player.PlayerEntity;
import net.minecraft.inventory.Inventory;
import net.minecraft.item.ItemStack;
import net.minecraft.screen.CraftingScreenHandler;
import net.minecraft.screen.PlayerScreenHandler;
import net.minecraft.screen.ScreenHandler;
import net.minecraft.screen.slot.Slot;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.text.Text;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.registry.Registry;
import net.minecraft.world.World;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * Loads contract K2 (config/mcdrift.json) and evaluates the six gates.
 *
 * gate kinds and their `require` values (see docs/contracts.md, K2):
 *   block_break : player_y<= (value) | held_match (value=item-id regex)
 *   craft_result: nearby_block (block, radius) | sky_visible
 *   furnace_tick: daytime | nighttime | base_block_stone  [optional input_match on slot 0]
 *
 * Failure semantics is always "action runs but produces NO output":
 *   block_break  -> cancel the break (no drop)
 *   craft_result -> result slot refuses to be taken (mineflayer craft yields nothing)
 *   furnace_tick -> smelting/burning progress is frozen
 */
public final class GateConfig {
    private static final Gson GSON = new Gson();
    private static final long HOT_RELOAD_MS = 2000L;
    private static final Set<Block> STONE_BASES = Set.of(
            Blocks.STONE, Blocks.COBBLESTONE, Blocks.SMOOTH_STONE,
            Blocks.DEEPSLATE, Blocks.STONE_BRICKS);

    private static volatile GateConfig INSTANCE = new GateConfig();
    private static volatile long lastMtime = -1L;
    private static volatile long lastCheck = 0L;

    // ----------------------------------------------------------------- state
    private String feedbackLevel = "minimal";
    private final List<Gate> gates = new ArrayList<>();
    private final Map<String, JsonObject> feedbackText = new LinkedHashMap<>();

    private record Gate(String id, String kind, JsonObject params, Pattern match) {}

    public static GateConfig get() { return INSTANCE; }

    public List<String> enabledIds() { return gates.stream().map(Gate::id).toList(); }

    // ----------------------------------------------------------------- loading
    public static Path configPath() {
        return FabricLoader.getInstance().getConfigDir().resolve("mcdrift.json");
    }

    public static synchronized void loadOrReload() {
        GateConfig fresh = new GateConfig();
        Path p = configPath();
        try {
            if (Files.exists(p)) {
                JsonObject root = GSON.fromJson(Files.readString(p), JsonObject.class);
                if (root.has("feedback_level"))
                    fresh.feedbackLevel = root.get("feedback_level").getAsString();
                JsonObject gatesObj = root.has("gates") ? root.getAsJsonObject("gates") : new JsonObject();
                if (root.has("enabled")) {
                    root.getAsJsonArray("enabled").forEach(idEl -> {
                        String id = idEl.getAsString();
                        if (!gatesObj.has(id)) {
                            McDrift.LOGGER.warn("[mcdrift] enabled id {} has no gates entry; skipping", id);
                            return;
                        }
                        JsonObject g = gatesObj.getAsJsonObject(id);
                        JsonObject params = g.getAsJsonObject("params");
                        String matchKey = params.has("block_match") ? "block_match"
                                        : params.has("result_match") ? "result_match"
                                        : params.has("input_match") ? "input_match" : null;
                        Pattern pat = matchKey == null ? null
                                        : Pattern.compile(params.get(matchKey).getAsString());
                        fresh.gates.add(new Gate(id, g.get("gate").getAsString(), params, pat));
                    });
                }
                if (root.has("feedback_text")) {
                    root.getAsJsonObject("feedback_text").entrySet().forEach(e ->
                            fresh.feedbackText.put(e.getKey(), e.getValue().getAsJsonObject()));
                }
                lastMtime = Files.getLastModifiedTime(p).toMillis();
            } else {
                McDrift.LOGGER.warn("[mcdrift] no config at {}; all gates disabled", p);
                lastMtime = -1L;
            }
        } catch (Exception exc) {
            McDrift.LOGGER.error("[mcdrift] failed to load {}: {} — keeping previous config",
                    p, exc.toString());
            return;
        }
        INSTANCE = fresh;
        McDrift.LOGGER.info("[mcdrift] config loaded; gates={}", fresh.enabledIds());
    }

    /** Cheap mtime-based hot reload, throttled to every {@value #HOT_RELOAD_MS} ms. */
    public static void maybeHotReload() {
        long now = System.currentTimeMillis();
        if (now - lastCheck < HOT_RELOAD_MS) return;
        lastCheck = now;
        try {
            Path p = configPath();
            long m = Files.exists(p) ? Files.getLastModifiedTime(p).toMillis() : -1L;
            if (m != lastMtime) loadOrReload();
        } catch (IOException ignored) { }
    }

    // ----------------------------------------------------------------- gate: block_break (X1, X2)
    public boolean allowBlockBreak(ServerPlayerEntity player, BlockPos pos,
                                   BlockState state, World world) {
        String blockId = Registry.BLOCK.getId(state.getBlock()).toString();
        for (Gate g : gates) {
            if (!g.kind().equals("block_break")) continue;
            if (g.match() == null || !g.match().matcher(blockId).matches()) continue;
            String require = g.params().get("require").getAsString();
            boolean pass = switch (require) {
                case "player_y<=" -> player.getY() <= g.params().get("value").getAsDouble();
                case "held_match" -> heldIdMatches(player, g.params().get("value").getAsString());
                default -> { warnRequire(g, require); yield true; }
            };
            AuditWriter.write(g.id(), pass ? "pass" : "block", "block_break", player,
                    "block=" + blockId);
            if (!pass) { feedback(player, g.id()); return false; }
        }
        return true;
    }

    // ----------------------------------------------------------------- gate: craft_result (P1, E2)
    public boolean allowCraftTake(PlayerEntity player, ItemStack result) {
        if (result == null || result.isEmpty()) return true;
        String resultId = Registry.ITEM.getId(result.getItem()).toString();
        for (Gate g : gates) {
            if (!g.kind().equals("craft_result")) continue;
            if (g.match() == null || !g.match().matcher(resultId).matches()) continue;
            String require = g.params().get("require").getAsString();
            boolean pass = switch (require) {
                case "nearby_block" -> nearbyBlock(player,
                        g.params().get("block").getAsString(),
                        g.params().get("radius").getAsInt());
                case "sky_visible" -> player.world.isSkyVisible(player.getBlockPos().up());
                case "inventory_min" -> inventoryMin(player,
                        g.params().get("item").getAsString(),
                        g.params().get("count").getAsInt());
                default -> { warnRequire(g, require); yield true; }
            };
            AuditWriter.write(g.id(), pass ? "pass" : "block", "craft_result", player,
                    "result=" + resultId);
            if (!pass) { feedback(player, g.id()); return false; }
        }
        return true;
    }

    // ----------------------------------------------------------------- gate: furnace_tick (P2, E1)
    public boolean allowFurnaceTick(World world, BlockPos pos, AbstractFurnaceBlockEntity be) {
        if (gates.isEmpty()) return true;
        maybeHotReload();
        ItemStack input = ((Inventory) be).getStack(0);
        for (Gate g : gates) {
            if (!g.kind().equals("furnace_tick")) continue;
            if (g.match() != null) {       // optional input_match filter
                if (input.isEmpty()) continue;
                String inputId = Registry.ITEM.getId(input.getItem()).toString();
                if (!g.match().matcher(inputId).matches()) continue;
            }
            String require = g.params().get("require").getAsString();
            boolean pass = switch (require) {
                case "daytime" -> (world.getTimeOfDay() % 24000L) < 12000L;
                case "nighttime" -> (world.getTimeOfDay() % 24000L) >= 12000L;
                case "base_block_stone" ->
                        STONE_BASES.contains(world.getBlockState(pos.down()).getBlock());
                default -> { warnRequire(g, require); yield true; }
            };
            // throttled audit (furnaces tick 20x/s)
            AuditWriter.writeFurnaceThrottled(g.id(), pass ? "pass" : "block", pos,
                    world.getTimeOfDay());
            if (!pass) return false;
        }
        return true;
    }

    // ----------------------------------------------------------------- helpers
    private static boolean heldIdMatches(PlayerEntity player, String regex) {
        ItemStack held = player.getMainHandStack();
        if (held.isEmpty()) return false;
        return Registry.ITEM.getId(held.getItem()).toString().matches(regex);
    }

    /** require=inventory_min: count carried item plus the active crafting input.
     *
     * Craft-result gates run from CraftingResultSlot.canTakeItems. At that
     * point mineflayer may already have moved recipe ingredients from the
     * player inventory into the crafting grid, so player.getInventory().count()
     * undercounts the pre-craft resources by the grid contents. Count only
     * non-player slots from the active crafting handler to recover the intended
     * "resources available for this craft" boundary without double counting the
     * player's main inventory/hotbar slots.
     */
    private static boolean inventoryMin(PlayerEntity player, String itemId, int count) {
        net.minecraft.item.Item item = Registry.ITEM.get(new net.minecraft.util.Identifier(itemId));
        int total = player.getInventory().count(item);
        if (player instanceof ServerPlayerEntity sp) {
            ScreenHandler h = sp.currentScreenHandler;
            if (h instanceof CraftingScreenHandler || h instanceof PlayerScreenHandler) {
                for (Slot slot : h.slots) {
                    if (slot.inventory == player.getInventory()) continue;
                    ItemStack stack = slot.getStack();
                    if (!stack.isEmpty() && stack.getItem() == item) {
                        total += stack.getCount();
                    }
                }
            }
        }
        return total >= count;
    }

    private static boolean nearbyBlock(PlayerEntity player, String blockId, int radius) {
        BlockPos base = player.getBlockPos();
        for (int dx = -radius; dx <= radius; dx++)
            for (int dy = -radius; dy <= radius; dy++)
                for (int dz = -radius; dz <= radius; dz++) {
                    BlockState s = player.world.getBlockState(base.add(dx, dy, dz));
                    if (Registry.BLOCK.getId(s.getBlock()).toString().equals(blockId)) return true;
                }
        return false;
    }

    /** INV-2 minimal failure feedback: say nothing unless typed/hinted is configured. */
    private void feedback(PlayerEntity player, String biasId) {
        if ("minimal".equals(feedbackLevel)) return;
        JsonObject t = feedbackText.get(biasId);
        if (t == null || !t.has(feedbackLevel)) return;
        player.sendMessage(Text.literal(t.get(feedbackLevel).getAsString()));
    }

    private static void warnRequire(Gate g, String require) {
        McDrift.LOGGER.warn("[mcdrift] gate {}: unknown require '{}' — treating as pass",
                g.id(), require);
    }
}
