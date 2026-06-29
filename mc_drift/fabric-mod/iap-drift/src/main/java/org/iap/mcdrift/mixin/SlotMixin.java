package org.iap.mcdrift.mixin;

import net.minecraft.entity.player.PlayerEntity;
import net.minecraft.item.ItemStack;
import net.minecraft.screen.slot.CraftingResultSlot;
import net.minecraft.screen.slot.FurnaceOutputSlot;
import net.minecraft.screen.slot.Slot;
import net.minecraft.server.network.ServerPlayerEntity;
import org.iap.mcdrift.gate.GateDecision;
import org.iap.mcdrift.gate.RuntimeGate;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

import java.util.Optional;

@Mixin(Slot.class)
public abstract class SlotMixin {
    @Shadow
    public abstract ItemStack getStack();

    @Inject(method = "canTakeItems", at = @org.spongepowered.asm.mixin.injection.At("HEAD"), cancellable = true)
    private void iapdrift$canTakeItems(PlayerEntity player, CallbackInfoReturnable<Boolean> cir) {
        if (iapdrift$denied(player, this.getStack())) {
            cir.setReturnValue(false);
        }
    }

    @Inject(method = "tryTakeStackRange", at = @org.spongepowered.asm.mixin.injection.At("HEAD"), cancellable = true)
    private void iapdrift$tryTakeStackRange(int min, int max, PlayerEntity player, CallbackInfoReturnable<Optional<ItemStack>> cir) {
        if (iapdrift$denied(player, this.getStack())) {
            cir.setReturnValue(Optional.empty());
        }
    }

    @Inject(method = "takeStackRange", at = @org.spongepowered.asm.mixin.injection.At("HEAD"), cancellable = true)
    private void iapdrift$takeStackRange(int min, int max, PlayerEntity player, CallbackInfoReturnable<ItemStack> cir) {
        if (iapdrift$denied(player, this.getStack())) {
            cir.setReturnValue(ItemStack.EMPTY);
        }
    }

    private boolean iapdrift$denied(PlayerEntity player, ItemStack stack) {
        if (!(player instanceof ServerPlayerEntity serverPlayer)) return false;
        Object slot = this;
        String eventName = null;
        if (slot instanceof CraftingResultSlot) {
            eventName = "crafting_output";
        } else if (slot instanceof FurnaceOutputSlot) {
            eventName = "smelting_output";
        }
        if (eventName == null) return false;
        GateDecision decision = RuntimeGate.evaluateOutputGate(eventName, serverPlayer, stack);
        return decision.matched && !decision.allowed;
    }
}
