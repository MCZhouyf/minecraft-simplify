package org.iap.mcdrift.config;

import java.util.ArrayList;
import java.util.List;

public class DriftTask {
    public String id = "";
    public boolean enabled = false;
    public String action = "";
    public String goal = "";
    public String family = "";
    public String event = "";
    public List<String> target_blocks = new ArrayList<>();
    public String ground_truth = "";
    public String origin = "";
    public String drift = "";
    public String public_failure_message = "Action failed under current environment condition.";

    public boolean isActiveBlockBreakTask() {
        return enabled && "block_break".equals(event) && target_blocks != null && !target_blocks.isEmpty();
    }
}
