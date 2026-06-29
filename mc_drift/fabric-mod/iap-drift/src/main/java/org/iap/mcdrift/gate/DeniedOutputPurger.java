package org.iap.mcdrift.gate;

import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.minecraft.item.ItemStack;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.network.ServerPlayerEntity;

import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.UUID;

public class DeniedOutputPurger {
    private static final List<Job> JOBS = new ArrayList<>();
    private static final List<Guard> GUARDS = new ArrayList<>();

    public static void register() {
        ServerTickEvents.END_SERVER_TICK.register(DeniedOutputPurger::onServerTick);
    }

    public static void schedule(ServerPlayerEntity player, ItemStack deniedStack) {
        if (player == null || deniedStack == null || deniedStack.isEmpty()) return;
        UUID playerId = player.getUuid();
        ItemStack target = deniedStack.copy();
        GUARDS.add(new Guard(playerId, target.copy(), 100));
        int[] delays = new int[] {0, 1, 2, 3, 5, 8, 13, 20, 30, 40, 60};
        for (int delay : delays) {
            JOBS.add(new Job(playerId, target.copy(), delay));
        }
    }

    private static void onServerTick(MinecraftServer server) {
        Iterator<Guard> guardIterator = GUARDS.iterator();
        while (guardIterator.hasNext()) {
            Guard guard = guardIterator.next();
            guard.ttlTicks -= 1;
            if (guard.ttlTicks <= 0) {
                guardIterator.remove();
            }
        }

        Iterator<Job> iterator = JOBS.iterator();
        while (iterator.hasNext()) {
            Job job = iterator.next();
            if (job.delayTicks > 0) {
                job.delayTicks -= 1;
                continue;
            }
            ServerPlayerEntity player = server.getPlayerManager().getPlayer(job.playerId);
            if (player != null) {
                purgeEverywhere(player, job.target);
            }
            iterator.remove();
        }
    }

    public static boolean isDenied(ServerPlayerEntity player, ItemStack stack) {
        if (player == null || stack == null || stack.isEmpty()) return false;
        for (Guard guard : GUARDS) {
            if (guard.playerId.equals(player.getUuid()) && ItemStack.areItemsEqual(stack, guard.target)) {
                return true;
            }
        }
        return false;
    }

    public static void purgeEverywhere(ServerPlayerEntity player, ItemStack target) {
        if (player == null || target == null || target.isEmpty()) return;
        for (int i = 0; i < player.getInventory().size(); i++) {
            ItemStack stack = player.getInventory().getStack(i);
            if (ItemStack.areItemsEqual(stack, target)) {
                player.getInventory().setStack(i, ItemStack.EMPTY);
            }
        }
        if (player.currentScreenHandler != null) {
            for (int i = 0; i < player.currentScreenHandler.slots.size(); i++) {
                ItemStack stack = player.currentScreenHandler.slots.get(i).getStack();
                if (ItemStack.areItemsEqual(stack, target)) {
                    player.currentScreenHandler.slots.get(i).setStack(ItemStack.EMPTY);
                    player.currentScreenHandler.slots.get(i).markDirty();
                }
            }
            player.currentScreenHandler.sendContentUpdates();
        }
        if (player.playerScreenHandler != null) {
            player.playerScreenHandler.sendContentUpdates();
        }
    }

    private static class Job {
        final UUID playerId;
        final ItemStack target;
        int delayTicks;

        Job(UUID playerId, ItemStack target, int delayTicks) {
            this.playerId = playerId;
            this.target = target;
            this.delayTicks = delayTicks;
        }
    }

    private static class Guard {
        final UUID playerId;
        final ItemStack target;
        int ttlTicks;

        Guard(UUID playerId, ItemStack target, int ttlTicks) {
            this.playerId = playerId;
            this.target = target;
            this.ttlTicks = ttlTicks;
        }
    }
}
