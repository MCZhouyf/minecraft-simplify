package org.iap.mcdrift.gate;

import com.google.gson.JsonObject;
import net.minecraft.item.ItemStack;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.server.world.ServerWorld;
import net.minecraft.text.Text;
import net.minecraft.util.Identifier;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.registry.Registry;
import org.iap.mcdrift.IapDriftMod;
import org.iap.mcdrift.config.DriftTask;
import org.iap.mcdrift.predicate.PredicateEvaluator;

public class RuntimeGate {
    public static GateDecision evaluateOutputGate(String eventName, ServerPlayerEntity player, ItemStack outputStack) {
        if (outputStack == null || outputStack.isEmpty()) return GateDecision.unmatched();
        if (!IapDriftMod.config().active) return GateDecision.unmatched();

        Identifier outputItem = Registry.ITEM.getId(outputStack.getItem());
        String outputItemName = outputItem.toString();

        for (DriftTask task : IapDriftMod.config().getActiveOutputTasks(eventName)) {
            if (task.target_items == null || !task.target_items.contains(outputItemName)) continue;

            ServerWorld world = (ServerWorld) player.getWorld();
            BlockPos pos = player.getBlockPos();
            PredicateEvaluator.EvalResult result = PredicateEvaluator.evaluate(task.ground_truth, player, world, pos);

            JsonObject event = new JsonObject();
            event.addProperty("task_id", task.id);
            event.addProperty("action", task.action);
            event.addProperty("event", eventName);
            event.addProperty("output_item", outputItemName);
            event.addProperty("ground_truth", task.ground_truth);
            event.addProperty("gate_value", result.passed);
            event.addProperty("gate_detail", result.detail);
            event.addProperty("allowed", result.passed);
            event.addProperty("player", player.getGameProfile().getName());
            IapDriftMod.truthLogger().log(event);

            if (!result.passed) {
                String msg = task.public_failure_message == null || task.public_failure_message.isBlank()
                        ? IapDriftMod.config().public_failure_message
                        : task.public_failure_message;
                player.sendMessage(Text.literal(msg), false);
                purgeDeniedOutput(player, outputStack);
                DeniedOutputPurger.schedule(player, outputStack);
                return GateDecision.deny();
            }
            return GateDecision.allow();
        }
        return GateDecision.unmatched();
    }

    public static void purgeDeniedOutput(ServerPlayerEntity player, ItemStack deniedStack) {
        if (player == null || deniedStack == null || deniedStack.isEmpty()) return;
        ItemStack target = deniedStack.copy();

        DeniedOutputPurger.purgeEverywhere(player, target);
        player.currentScreenHandler.sendContentUpdates();
        player.playerScreenHandler.sendContentUpdates();

    }
}
