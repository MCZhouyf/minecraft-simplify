package org.iap.mcdrift.gate;

public class GateDecision {
    public final boolean matched;
    public final boolean allowed;

    private GateDecision(boolean matched, boolean allowed) {
        this.matched = matched;
        this.allowed = allowed;
    }

    public static GateDecision unmatched() { return new GateDecision(false, true); }
    public static GateDecision allow() { return new GateDecision(true, true); }
    public static GateDecision deny() { return new GateDecision(true, false); }
}
