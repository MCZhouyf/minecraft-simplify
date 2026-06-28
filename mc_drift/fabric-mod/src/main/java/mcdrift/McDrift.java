package mcdrift;

import com.mojang.brigadier.Command;
import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.command.v2.CommandRegistrationCallback;
import net.fabricmc.fabric.api.event.player.PlayerBlockBreakEvents;
import net.minecraft.server.command.CommandManager;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.text.Text;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * MC-Drift Stage 2: engine-level event gates for the 6 mod_event biases
 * (P1, P2, X1, X2, E1, E2), driven by config/mcdrift.json (contract K2)
 * and writing the K8 audit log to logs/mcdrift_audit.jsonl.
 *
 * Runs on the integrated server of a LAN single-player world: all gate
 * checks are server-side only (world.isClient guards everywhere).
 *
 * Config is hot-reloaded on file mtime change (checked at most every 2s),
 * and can be forced with the in-game command:  /mcdrift reload
 *
 * NOTE (1.19 / Fabric API 0.58.0): if CommandRegistrationCallback fails to
 * compile with the 3-arg lambda below, your Fabric API predates command-api-v2.
 * Fallback: change the import to
 *   net.fabricmc.fabric.api.command.v1.CommandRegistrationCallback
 * and the lambda to  (dispatcher, dedicated) -> { ... } .
 * Hot reload keeps working either way, so the command is a convenience only.
 */
public class McDrift implements ModInitializer {
    public static final Logger LOGGER = LoggerFactory.getLogger("mcdrift");

    @Override
    public void onInitialize() {
        GateConfig.loadOrReload();
        LOGGER.info("[mcdrift] initialized; enabled gates: {}", GateConfig.get().enabledIds());

        // ---- block_break gates (X1, X2) — pure Fabric API, no mixin needed ----
        PlayerBlockBreakEvents.BEFORE.register((world, player, pos, state, blockEntity) -> {
            if (world.isClient()) return true;
            GateConfig.maybeHotReload();
            if (!(player instanceof ServerPlayerEntity serverPlayer)) return true;
            // returning false cancels the break -> block stays -> NO drop -> "no output" failure
            return GateConfig.get().allowBlockBreak(serverPlayer, pos, state, world);
        });

        // ---- /mcdrift reload ----
        CommandRegistrationCallback.EVENT.register((dispatcher, registryAccess, environment) ->
            dispatcher.register(
                CommandManager.literal("mcdrift")
                    .requires(src -> src.hasPermissionLevel(2))
                    .then(CommandManager.literal("reload").executes(ctx -> {
                        GateConfig.loadOrReload();
                        ctx.getSource().sendFeedback(
                            Text.literal("[mcdrift] config reloaded: " + GateConfig.get().enabledIds()),
                            false);
                        return Command.SINGLE_SUCCESS;
                    }))));
    }
}
