package org.iap.mcdrift.mixin;

import net.minecraft.entity.player.PlayerEntity;
import net.minecraft.item.ItemStack;
import net.minecraft.screen.ScreenHandler;
import net.minecraft.screen.slot.CraftingResultSlot;
import net.minecraft.screen.slot.FurnaceOutputSlot;
import net.minecraft.screen.slot.Slot;
import net.minecraft.screen.slot.SlotActionType;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.util.collection.DefaultedList;
import org.iap.mcdrift.gate.GateDecision;
import org.iap.mcdrift.gate.RuntimeGate;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(ScreenHandler.class)
public abstract class ScreenHandlerMixin {
    @Shadow
    public DefaultedList<Slot> slots;

    @Inject(method = "onSlotClick", at = @org.spongepowered.asm.mixin.injection.At("HEAD"), cancellable = true)
    private void iapdrift$onSlotClick(int slotIndex, int button, SlotActionType actionType, PlayerEntity player, CallbackInfo ci) {
        if (!(player instanceof ServerPlayerEntity serverPlayer)) return;
        if (slotIndex < 0 || slotIndex >= this.slots.size()) return;

        Slot slot = this.slots.get(slotIndex);
        String eventName = null;
        if (slot instanceof CraftingResultSlot) {
            eventName = "crafting_output";
        } else if (slot instanceof FurnaceOutputSlot) {
            eventName = "smelting_output";
        }
        if (eventName == null) return;

        ItemStack stack = slot.getStack();
        GateDecision decision = RuntimeGate.evaluateOutputGate(eventName, serverPlayer, stack);
        if (decision.matched && !decision.allowed) {
            RuntimeGate.purgeDeniedOutput(serverPlayer, stack);
            slot.setStack(ItemStack.EMPTY);
            slot.markDirty();
            ci.cancel();
        }
    }
}
