package org.iap.mcdrift.mixin;

import net.minecraft.entity.player.PlayerEntity;
import net.minecraft.entity.player.PlayerInventory;
import net.minecraft.item.ItemStack;
import net.minecraft.server.network.ServerPlayerEntity;
import org.iap.mcdrift.gate.DeniedOutputPurger;
import org.spongepowered.asm.mixin.Final;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

@Mixin(PlayerInventory.class)
public abstract class PlayerInventoryMixin {
    @Shadow
    @Final
    public PlayerEntity player;

    @Inject(method = "insertStack(Lnet/minecraft/item/ItemStack;)Z", at = @org.spongepowered.asm.mixin.injection.At("HEAD"), cancellable = true)
    private void iapdrift$insertStack(ItemStack stack, CallbackInfoReturnable<Boolean> cir) {
        if (iapdrift$denied(stack)) {
            stack.setCount(0);
            cir.setReturnValue(false);
        }
    }

    @Inject(method = "insertStack(ILnet/minecraft/item/ItemStack;)Z", at = @org.spongepowered.asm.mixin.injection.At("HEAD"), cancellable = true)
    private void iapdrift$insertStackAt(int slot, ItemStack stack, CallbackInfoReturnable<Boolean> cir) {
        if (iapdrift$denied(stack)) {
            stack.setCount(0);
            cir.setReturnValue(false);
        }
    }

    @Inject(method = "offerOrDrop", at = @org.spongepowered.asm.mixin.injection.At("HEAD"), cancellable = true)
    private void iapdrift$offerOrDrop(ItemStack stack, CallbackInfo ci) {
        if (iapdrift$denied(stack)) {
            stack.setCount(0);
            ci.cancel();
        }
    }

    @Inject(method = "offer", at = @org.spongepowered.asm.mixin.injection.At("HEAD"), cancellable = true)
    private void iapdrift$offer(ItemStack stack, boolean notifiesClient, CallbackInfo ci) {
        if (iapdrift$denied(stack)) {
            stack.setCount(0);
            ci.cancel();
        }
    }

    private boolean iapdrift$denied(ItemStack stack) {
        return this.player instanceof ServerPlayerEntity serverPlayer
                && DeniedOutputPurger.isDenied(serverPlayer, stack);
    }
}
