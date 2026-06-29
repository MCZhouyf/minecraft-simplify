package org.iap.mcdrift.predicate;

import net.minecraft.entity.Entity;
import net.minecraft.item.ItemStack;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.server.world.ServerWorld;
import net.minecraft.util.Identifier;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Box;
import net.minecraft.util.registry.Registry;

import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class PredicateEvaluator {
    private static final Pattern Y_LEVEL_LEQ = Pattern.compile("^y_level\\(y\\)\\s*<=\\s*(-?\\d+)$");
    private static final Pattern NEARBY_BLOCK = Pattern.compile("^nearby_block\\(([a-z0-9_]+)\\)\\s*<=k\\s*(\\d+)$");
    private static final Pattern NEARBY_ENTITY = Pattern.compile("^nearby_entity\\(([a-z0-9_]+)\\)\\s*<=k\\s*(\\d+)$");
    private static final Pattern TIME_WINDOW = Pattern.compile("^time_of_day\\(time\\)\\s+in\\s+\\[(\\d+),\\s*(\\d+)\\]$");
    private static final Pattern HELD_ITEM = Pattern.compile("^held_item\\(type\\)\\s*=\\s*([a-z0-9_]+)$");
    private static final Pattern HELD_TOOL = Pattern.compile("^held_tool\\(tier\\)\\s*>=\\s*([a-z0-9_]+)$");
    private static final Pattern STATION_BASE_BLOCK = Pattern.compile("^station_base_block\\(type\\)\\s*=\\s*([a-z0-9_]+)$");

    public static EvalResult evaluate(String predicate, ServerPlayerEntity player, ServerWorld world, BlockPos targetPos) {
        String normalized = predicate == null ? "" : predicate.trim().replaceAll("\\s+", " ");

        Matcher yMatcher = Y_LEVEL_LEQ.matcher(normalized);
        if (yMatcher.matches()) {
            int threshold = Integer.parseInt(yMatcher.group(1));
            int y = targetPos != null ? targetPos.getY() : player.getBlockY();
            return new EvalResult(y <= threshold, "y=" + y + ", threshold=" + threshold);
        }

        Matcher blockMatcher = NEARBY_BLOCK.matcher(normalized);
        if (blockMatcher.matches()) {
            String block = blockMatcher.group(1);
            int radius = Integer.parseInt(blockMatcher.group(2));
            boolean found = hasNearbyBlock(world, player.getBlockPos(), block, radius);
            return new EvalResult(found, "nearby_block=" + block + ", radius=" + radius + ", found=" + found);
        }

        Matcher entityMatcher = NEARBY_ENTITY.matcher(normalized);
        if (entityMatcher.matches()) {
            String entity = entityMatcher.group(1);
            int radius = Integer.parseInt(entityMatcher.group(2));
            boolean found = hasNearbyEntity(world, player, entity, radius);
            return new EvalResult(found, "nearby_entity=" + entity + ", radius=" + radius + ", found=" + found);
        }

        Matcher timeMatcher = TIME_WINDOW.matcher(normalized);
        if (timeMatcher.matches()) {
            long t = world.getTimeOfDay() % 24000L;
            int start = Integer.parseInt(timeMatcher.group(1));
            int end = Integer.parseInt(timeMatcher.group(2));
            boolean passed = inTimeWindow(t, start, end);
            return new EvalResult(passed, "time=" + t + ", window=[" + start + "," + end + "]");
        }

        Matcher heldItemMatcher = HELD_ITEM.matcher(normalized);
        if (heldItemMatcher.matches()) {
            String expected = heldItemMatcher.group(1);
            ItemStack stack = player.getMainHandStack();
            Identifier held = Registry.ITEM.getId(stack.getItem());
            boolean passed = idMatches(held, expected);
            return new EvalResult(passed, "held_item=" + held + ", expected=" + expected);
        }

        Matcher heldToolMatcher = HELD_TOOL.matcher(normalized);
        if (heldToolMatcher.matches()) {
            String minTier = heldToolMatcher.group(1);
            Identifier held = Registry.ITEM.getId(player.getMainHandStack().getItem());
            int heldLevel = toolTierLevel(held.getPath());
            int minLevel = toolTierLevel(minTier + "_pickaxe");
            boolean passed = heldLevel >= minLevel;
            return new EvalResult(passed, "held_tool=" + held + ", held_level=" + heldLevel + ", min_tier=" + minTier);
        }

        Matcher baseMatcher = STATION_BASE_BLOCK.matcher(normalized);
        if (baseMatcher.matches()) {
            String expected = baseMatcher.group(1);
            Identifier below = Registry.BLOCK.getId(world.getBlockState(player.getBlockPos().down()).getBlock());
            boolean passed = idMatches(below, expected);
            return new EvalResult(passed, "station_base_block=" + below + ", expected=" + expected);
        }

        return new EvalResult(false, "unsupported predicate in Phase 5-6: " + normalized);
    }

    private static boolean hasNearbyBlock(ServerWorld world, BlockPos center, String wanted, int radius) {
        BlockPos.Mutable mutable = new BlockPos.Mutable();
        for (int dx = -radius; dx <= radius; dx++) {
            for (int dy = -radius; dy <= radius; dy++) {
                for (int dz = -radius; dz <= radius; dz++) {
                    mutable.set(center.getX() + dx, center.getY() + dy, center.getZ() + dz);
                    Identifier id = Registry.BLOCK.getId(world.getBlockState(mutable).getBlock());
                    if (idMatches(id, wanted)) return true;
                }
            }
        }
        return false;
    }

    private static boolean hasNearbyEntity(ServerWorld world, ServerPlayerEntity player, String wanted, int radius) {
        Box box = Box.of(player.getPos(), radius * 2.0 + 1.0, radius * 2.0 + 1.0, radius * 2.0 + 1.0);
        for (Entity entity : world.getEntitiesByClass(Entity.class, box, e -> true)) {
            Identifier id = Registry.ENTITY_TYPE.getId(entity.getType());
            if (idMatches(id, wanted)) return true;
        }
        return false;
    }

    private static boolean inTimeWindow(long t, int start, int end) {
        if (start <= end) return t >= start && t <= end;
        return t >= start || t <= end;
    }

    private static boolean idMatches(Identifier id, String wanted) {
        String normalized = wanted.toLowerCase(Locale.ROOT);
        return id.getPath().equals(normalized)
                || id.toString().equals("minecraft:" + normalized)
                || id.toString().equals(normalized);
    }

    private static int toolTierLevel(String itemPath) {
        if (itemPath == null) return -1;
        if (itemPath.startsWith("netherite_")) return 4;
        if (itemPath.startsWith("diamond_")) return 3;
        if (itemPath.startsWith("iron_")) return 2;
        if (itemPath.startsWith("stone_")) return 1;
        if (itemPath.startsWith("golden_")) return 0;
        if (itemPath.startsWith("wooden_")) return 0;
        return -1;
    }

    public static class EvalResult {
        public final boolean passed;
        public final String detail;
        public EvalResult(boolean passed, String detail) {
            this.passed = passed;
            this.detail = detail;
        }
    }
}
