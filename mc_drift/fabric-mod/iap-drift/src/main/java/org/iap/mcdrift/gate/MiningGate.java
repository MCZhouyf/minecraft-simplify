package org.iap.mcdrift.gate;

import com.google.gson.JsonObject;
import net.fabricmc.fabric.api.event.player.PlayerBlockBreakEvents;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.server.world.ServerWorld;
import net.minecraft.text.Text;
import net.minecraft.util.Identifier;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.registry.Registry;
import org.iap.mcdrift.IapDriftMod;
import org.iap.mcdrift.config.DriftTask;
import org.iap.mcdrift.predicate.PredicateEvaluator;

public class MiningGate {
    public static void register() {
        PlayerBlockBreakEvents.BEFORE.register((world, player, pos, state, blockEntity) -> {
            if (world.isClient()) return true;
            if (!(player instanceof ServerPlayerEntity serverPlayer) || !(world instanceof ServerWorld serverWorld)) return true;
            if (!IapDriftMod.config().active) return true;

            Identifier blockId = Registry.BLOCK.getId(state.getBlock());
            String blockName = blockId.toString();

            for (DriftTask task : IapDriftMod.config().getActiveBlockBreakTasks()) {
                if (task.target_blocks == null || !task.target_blocks.contains(blockName)) continue;

                PredicateEvaluator.EvalResult result =
                        PredicateEvaluator.evaluate(task.ground_truth, serverPlayer, serverWorld, pos);

                JsonObject event = new JsonObject();
                event.addProperty("task_id", task.id);
                event.addProperty("action", task.action);
                event.addProperty("event", "block_break");
                event.addProperty("target_block", blockName);
                event.addProperty("x", pos.getX());
                event.addProperty("y", pos.getY());
                event.addProperty("z", pos.getZ());
                event.addProperty("ground_truth", task.ground_truth);
                event.addProperty("gate_value", result.passed);
                event.addProperty("gate_detail", result.detail);
                event.addProperty("allowed", result.passed);
                event.addProperty("player", serverPlayer.getGameProfile().getName());
                IapDriftMod.truthLogger().log(event);

                if (!result.passed) {
                    String message = task.public_failure_message == null || task.public_failure_message.isBlank()
                            ? IapDriftMod.config().public_failure_message
                            : task.public_failure_message;
                    serverPlayer.sendMessage(Text.literal(message), false);
                    return false;
                }
                return true;
            }
            return true;
        });
    }
}
