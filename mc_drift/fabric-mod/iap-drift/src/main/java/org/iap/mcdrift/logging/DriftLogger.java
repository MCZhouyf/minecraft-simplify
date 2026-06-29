package org.iap.mcdrift.logging;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import net.fabricmc.loader.api.FabricLoader;
import org.iap.mcdrift.IapDriftMod;

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import static java.nio.file.StandardOpenOption.APPEND;
import static java.nio.file.StandardOpenOption.CREATE;

public class DriftLogger {
    private final Gson gson = new Gson();
    private final Path logPath;

    public DriftLogger(String relativeLogPath) {
        Path gameDir = FabricLoader.getInstance().getGameDir();
        this.logPath = gameDir.resolve(relativeLogPath == null || relativeLogPath.isBlank()
                ? "iap_drift_logs/truth.jsonl"
                : relativeLogPath);
        try {
            Files.createDirectories(this.logPath.getParent());
        } catch (IOException e) {
            IapDriftMod.LOG.error("[IAP-Drift] Failed to create log directory for {}", this.logPath, e);
        }
    }

    public synchronized void log(JsonObject event) {
        event.addProperty("timestamp_ms", System.currentTimeMillis());
        try (BufferedWriter writer = Files.newBufferedWriter(logPath, StandardCharsets.UTF_8, CREATE, APPEND)) {
            writer.write(gson.toJson(event));
            writer.newLine();
        } catch (IOException e) {
            IapDriftMod.LOG.error("[IAP-Drift] Failed to write truth log {}", logPath, e);
        }
    }

    public Path getLogPath() { return logPath; }
}
