package mcdrift;

import com.google.gson.JsonObject;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.entity.player.PlayerEntity;
import net.minecraft.item.ItemStack;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.registry.Registry;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Contract K8: one JSON object per line, appended to <gameDir>/logs/mcdrift_audit.jsonl.
 * The evaluator (experiments/evaluate.py) cross-checks this log against the agent-side
 * K7 log; a mismatch in failure attribution is a data-quality gate.
 */
public final class AuditWriter {
    private static final Path LOG = FabricLoader.getInstance().getGameDir()
            .resolve("logs").resolve("mcdrift_audit.jsonl");
    /** furnaces tick 20x per second: log each (pos,bias) at most every N game ticks. */
    private static final long FURNACE_THROTTLE_TICKS = 100L;
    private static final Map<String, Long> lastFurnaceLog = new ConcurrentHashMap<>();

    private AuditWriter() {}

    public static void write(String biasId, String decision, String event,
                             PlayerEntity player, String detail) {
        JsonObject o = base(biasId, decision, event, detail);
        JsonObject ps = new JsonObject();
        ps.addProperty("y", player.getY());
        ItemStack held = player.getMainHandStack();
        ps.addProperty("held", held.isEmpty() ? "empty"
                : Registry.ITEM.getId(held.getItem()).toString());
        ps.addProperty("time_of_day", player.world.getTimeOfDay() % 24000L);
        ps.addProperty("sky_visible", player.world.isSkyVisible(player.getBlockPos().up()));
        o.add("player_state", ps);
        append(o);
    }

    public static void writeFurnaceThrottled(String biasId, String decision,
                                             BlockPos pos, long worldTime) {
        String key = biasId + "@" + pos.toShortString();
        Long last = lastFurnaceLog.get(key);
        if (last != null && worldTime - last < FURNACE_THROTTLE_TICKS) return;
        lastFurnaceLog.put(key, worldTime);
        JsonObject o = base(biasId, decision, "furnace_tick",
                "pos=" + pos.toShortString() + " time=" + (worldTime % 24000L));
        append(o);
    }

    private static JsonObject base(String biasId, String decision, String event, String detail) {
        JsonObject o = new JsonObject();
        o.addProperty("ts", System.currentTimeMillis() / 1000.0);
        o.addProperty("bias_id", biasId);
        o.addProperty("decision", decision);
        o.addProperty("event", event);
        o.addProperty("detail", detail);
        return o;
    }

    private static synchronized void append(JsonObject o) {
        try {
            Files.createDirectories(LOG.getParent());
            Files.writeString(LOG, o.toString() + "\n", StandardCharsets.UTF_8,
                    StandardOpenOption.CREATE, StandardOpenOption.APPEND);
        } catch (IOException exc) {
            McDrift.LOGGER.error("[mcdrift] audit write failed: {}", exc.toString());
        }
    }
}
