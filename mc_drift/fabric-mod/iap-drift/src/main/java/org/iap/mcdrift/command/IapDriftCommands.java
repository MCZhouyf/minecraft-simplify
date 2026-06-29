package org.iap.mcdrift.command;

import com.mojang.brigadier.arguments.StringArgumentType;
import com.mojang.brigadier.builder.LiteralArgumentBuilder;
import net.fabricmc.fabric.api.command.v2.CommandRegistrationCallback;
import net.minecraft.server.command.ServerCommandSource;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.server.world.ServerWorld;
import net.minecraft.text.Text;
import org.iap.mcdrift.IapDriftMod;
import org.iap.mcdrift.predicate.PredicateEvaluator;

import static net.minecraft.server.command.CommandManager.argument;
import static net.minecraft.server.command.CommandManager.literal;

public class IapDriftCommands {
    public static void register() {
        CommandRegistrationCallback.EVENT.register((dispatcher, registryAccess, environment) -> {
            LiteralArgumentBuilder<ServerCommandSource> root = literal("iapdrift")
                    .requires(source -> source.hasPermissionLevel(2))
                    .then(literal("status").executes(context -> {
                        ServerCommandSource source = context.getSource();
                        source.sendFeedback(Text.literal("[IAP-Drift] active=" + IapDriftMod.config().active), false);
                        source.sendFeedback(Text.literal("[IAP-Drift] phase=" + IapDriftMod.config().phase), false);
                        source.sendFeedback(Text.literal("[IAP-Drift] total tasks=" + IapDriftMod.config().tasks.size()
                                + ", enabled=" + IapDriftMod.config().enabledTaskCount()
                                + ", block-break=" + IapDriftMod.config().getActiveBlockBreakTasks().size()
                                + ", crafting-output=" + IapDriftMod.config().getActiveOutputTasks("crafting_output").size()
                                + ", smelting-output=" + IapDriftMod.config().getActiveOutputTasks("smelting_output").size()), false);
                        source.sendFeedback(Text.literal("[IAP-Drift] truth log=" + IapDriftMod.truthLogger().getLogPath()), false);
                        return 1;
                    }))
                    .then(literal("reload").executes(context -> {
                        IapDriftMod.reloadConfig();
                        context.getSource().sendFeedback(Text.literal("[IAP-Drift] config reloaded."), false);
                        return 1;
                    }))
                    .then(literal("dump").executes(context -> {
                        ServerCommandSource source = context.getSource();
                        IapDriftMod.config().tasks.values().stream()
                                .filter(task -> task.enabled)
                                .forEach(task -> source.sendFeedback(Text.literal("[IAP-Drift] "
                                        + task.id + " event=" + task.event
                                        + " action=" + task.action
                                        + " gt=" + task.ground_truth), false));
                        return 1;
                    }))
                    .then(literal("eval")
                            .then(argument("predicate", StringArgumentType.greedyString()).executes(context -> {
                                ServerPlayerEntity player = context.getSource().getPlayer();
                                ServerWorld world = (ServerWorld) player.getWorld();
                                String predicate = StringArgumentType.getString(context, "predicate");
                                PredicateEvaluator.EvalResult result =
                                        PredicateEvaluator.evaluate(predicate, player, world, player.getBlockPos());
                                context.getSource().sendFeedback(Text.literal("[IAP-Drift] eval=" + result.passed
                                        + " detail=" + result.detail), false);
                                return result.passed ? 1 : 0;
                            })));
            dispatcher.register(root);
        });
    }
}
