package mcdrift.mixin;

import mcdrift.GateConfig;
import net.minecraft.entity.player.PlayerEntity;
import net.minecraft.screen.ScreenHandler;
import net.minecraft.screen.slot.CraftingResultSlot;
import net.minecraft.screen.slot.Slot;
import net.minecraft.screen.slot.SlotActionType;
import net.minecraft.util.collection.DefaultedList;
import org.spongepowered.asm.mixin.Final;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * Cancel result-slot clicks before vanilla moves the crafted item into a player
 * inventory. This covers Mineflayer's clickWindow path.
 */
@Mixin(ScreenHandler.class)
public abstract class ScreenHandlerCraftResultClickMixin {
    @Shadow @Final public DefaultedList<Slot> slots;

    @Shadow public abstract void sendContentUpdates();

    @Inject(method = "onSlotClick(IILnet/minecraft/screen/slot/SlotActionType;Lnet/minecraft/entity/player/PlayerEntity;)V",
            at = @At("HEAD"), cancellable = true)
    private void mcdrift$gateCraftResultClick(int slotIndex, int button,
                                              SlotActionType actionType,
                                              PlayerEntity player,
                                              CallbackInfo ci) {
        if (player.world == null || player.world.isClient()) return;
        if (slotIndex < 0 || slotIndex >= slots.size()) return;
        if (actionType != SlotActionType.PICKUP && actionType != SlotActionType.QUICK_MOVE) return;

        Slot slot = slots.get(slotIndex);
        if (!(slot instanceof CraftingResultSlot)) return;

        GateConfig.maybeHotReload();
        if (!GateConfig.get().allowCraftTake(player, slot.getStack())) {
            sendContentUpdates();
            ci.cancel();
        }
    }
}
