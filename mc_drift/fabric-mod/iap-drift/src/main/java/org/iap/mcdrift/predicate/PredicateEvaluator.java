package org.iap.mcdrift.predicate;

import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.server.world.ServerWorld;
import net.minecraft.util.math.BlockPos;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class PredicateEvaluator {
    private static final Pattern Y_LEVEL_LEQ =
            Pattern.compile("^y_level\\(y\\)\\s*<=\\s*(-?\\d+)$");

    public static EvalResult evaluate(String predicate, ServerPlayerEntity player, ServerWorld world, BlockPos targetPos) {
        String normalized = predicate == null ? "" : predicate.trim().replaceAll("\\s+", " ");
        Matcher yMatcher = Y_LEVEL_LEQ.matcher(normalized);
        if (yMatcher.matches()) {
            int threshold = Integer.parseInt(yMatcher.group(1));
            int y = targetPos != null ? targetPos.getY() : player.getBlockY();
            boolean passed = y <= threshold;
            return new EvalResult(passed, "y=" + y + ", threshold=" + threshold);
        }

        return new EvalResult(false, "unsupported predicate in Phase 3-4: " + normalized);
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
