package org.iap.mcdrift.command;

import com.mojang.brigadier.builder.LiteralArgumentBuilder;
import net.fabricmc.fabric.api.command.v2.CommandRegistrationCallback;
import net.minecraft.server.command.ServerCommandSource;
import net.minecraft.text.Text;
import org.iap.mcdrift.IapDriftMod;

import static net.minecraft.server.command.CommandManager.literal;

public class IapDriftCommands {
    public static void register() {
        CommandRegistrationCallback.EVENT.register((dispatcher, registryAccess, environment) -> {
            LiteralArgumentBuilder<ServerCommandSource> root = literal("iapdrift")
                    .requires(source -> source.hasPermissionLevel(2))
                    .then(literal("status").executes(context -> {
                        ServerCommandSource source = context.getSource();
                        source.sendFeedback(Text.literal("[IAP-Drift] active=" + IapDriftMod.config().active), false);
                        source.sendFeedback(Text.literal("[IAP-Drift] total tasks=" + IapDriftMod.config().tasks.size()
                                + ", enabled=" + IapDriftMod.config().enabledTaskCount()
                                + ", active block-break gates=" + IapDriftMod.config().getActiveBlockBreakTasks().size()), false);
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
                        IapDriftMod.config().getActiveBlockBreakTasks().forEach(task ->
                                source.sendFeedback(Text.literal("[IAP-Drift] " + task.id + " "
                                        + task.action + " " + task.ground_truth + " targets=" + task.target_blocks), false));
                        return 1;
                    }));

            dispatcher.register(root);
        });
    }
}
