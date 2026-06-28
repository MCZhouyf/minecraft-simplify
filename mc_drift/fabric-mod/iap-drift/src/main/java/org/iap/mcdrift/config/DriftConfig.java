package org.iap.mcdrift.config;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import net.fabricmc.loader.api.FabricLoader;
import org.iap.mcdrift.IapDriftMod;

import java.io.IOException;
import java.io.Reader;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

public class DriftConfig {
    public int version = 1;
    public boolean active = true;
    public String phase = "3-4";
    public String public_failure_message = "Action failed under current environment condition.";
    public String truth_log_file = "iap_drift_logs/truth.jsonl";
    public Map<String, DriftTask> tasks = new LinkedHashMap<>();

    private static final Gson GSON = new GsonBuilder().setPrettyPrinting().create();

    public static Path configPath() {
        return FabricLoader.getInstance().getConfigDir().resolve("iap-drift").resolve("tasks.json");
    }

    public static DriftConfig loadOrCreate() {
        Path path = configPath();
        try {
            Files.createDirectories(path.getParent());
            if (!Files.exists(path)) {
                DriftConfig empty = new DriftConfig();
                try (Writer writer = Files.newBufferedWriter(path, StandardCharsets.UTF_8)) {
                    GSON.toJson(empty, writer);
                }
                IapDriftMod.LOG.warn("[IAP-Drift] Created empty config at {}. Copy generated tasks.json here.", path);
                return empty;
            }

            try (Reader reader = Files.newBufferedReader(path, StandardCharsets.UTF_8)) {
                DriftConfig loaded = GSON.fromJson(reader, DriftConfig.class);
                if (loaded == null) {
                    throw new IOException("Parsed config is null.");
                }
                if (loaded.tasks == null) {
                    loaded.tasks = new LinkedHashMap<>();
                }
                return loaded;
            }
        } catch (IOException e) {
            IapDriftMod.LOG.error("[IAP-Drift] Failed to load config {}; using empty config.", path, e);
            return new DriftConfig();
        }
    }

    public List<DriftTask> getActiveBlockBreakTasks() {
        if (!active || tasks == null) {
            return List.of();
        }
        return tasks.values().stream()
                .filter(DriftTask::isActiveBlockBreakTask)
                .collect(Collectors.toList());
    }

    public int enabledTaskCount() {
        if (tasks == null) {
            return 0;
        }
        int count = 0;
        for (DriftTask task : tasks.values()) {
            if (task.enabled) {
                count++;
            }
        }
        return count;
    }
}
