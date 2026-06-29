package org.iap.mcdrift.mixin;

import net.minecraft.entity.player.PlayerEntity;
import net.minecraft.item.ItemStack;
import net.minecraft.screen.slot.CraftingResultSlot;
import net.minecraft.screen.slot.Slot;
import net.minecraft.server.network.ServerPlayerEntity;
import org.iap.mcdrift.gate.GateDecision;
import org.iap.mcdrift.gate.RuntimeGate;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(CraftingResultSlot.class)
public abstract class CraftingResultSlotMixin extends Slot {
    @Shadow
    private PlayerEntity player;

    public CraftingResultSlotMixin(net.minecraft.inventory.Inventory inventory, int index, int x, int y) {
        super(inventory, index, x, y);
    }

    @Inject(method = "takeStack(I)Lnet/minecraft/item/ItemStack;", at = @org.spongepowered.asm.mixin.injection.At("HEAD"), cancellable = true)
    private void iapdrift$takeStack(int amount, CallbackInfoReturnable<ItemStack> cir) {
        if (iapdrift$denied(this.player, this.getStack())) {
            cir.setReturnValue(ItemStack.EMPTY);
        }
    }

    @Inject(method = "onTakeItem", at = @org.spongepowered.asm.mixin.injection.At("HEAD"), cancellable = true)
    private void iapdrift$onTakeItem(PlayerEntity player, ItemStack stack, CallbackInfo ci) {
        if (iapdrift$denied(player, stack)) {
            if (player instanceof ServerPlayerEntity serverPlayer && !stack.isEmpty()) {
                serverPlayer.getInventory().remove(candidate -> ItemStack.areItemsEqual(candidate, stack), stack.getCount(), null);
            }
            ci.cancel();
        }
    }

    private boolean iapdrift$denied(PlayerEntity candidatePlayer, ItemStack stack) {
        if (!(candidatePlayer instanceof ServerPlayerEntity serverPlayer)) return false;
        GateDecision decision = RuntimeGate.evaluateOutputGate("crafting_output", serverPlayer, stack);
        return decision.matched && !decision.allowed;
    }
}
