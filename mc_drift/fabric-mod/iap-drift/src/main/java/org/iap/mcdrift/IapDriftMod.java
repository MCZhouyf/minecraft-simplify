package org.iap.mcdrift;

import net.fabricmc.api.ModInitializer;
import org.iap.mcdrift.command.IapDriftCommands;
import org.iap.mcdrift.config.DriftConfig;
import org.iap.mcdrift.gate.MiningGate;
import org.iap.mcdrift.logging.DriftLogger;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public class IapDriftMod implements ModInitializer {
    public static final String MOD_ID = "iap-drift";
    public static final Logger LOG = LoggerFactory.getLogger(MOD_ID);

    private static DriftConfig config;
    private static DriftLogger truthLogger;

    @Override
    public void onInitialize() {
        config = DriftConfig.loadOrCreate();
        truthLogger = new DriftLogger(config.truth_log_file);

        MiningGate.register();
        IapDriftCommands.register();

        LOG.info("[IAP-Drift] Loaded config with {} tasks, {} active block-break gates.",
                config.tasks.size(), config.getActiveBlockBreakTasks().size());
    }

    public static DriftConfig config() {
        return config;
    }

    public static DriftLogger truthLogger() {
        return truthLogger;
    }

    public static void reloadConfig() {
        config = DriftConfig.loadOrCreate();
        if (truthLogger == null) {
            truthLogger = new DriftLogger(config.truth_log_file);
        }
        LOG.info("[IAP-Drift] Reloaded config with {} tasks, {} active block-break gates.",
                config.tasks.size(), config.getActiveBlockBreakTasks().size());
    }
}
