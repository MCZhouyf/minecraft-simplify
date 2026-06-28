package mcdrift.mixin;

import mcdrift.GateConfig;
import net.minecraft.entity.player.PlayerEntity;
import net.minecraft.item.ItemStack;
import net.minecraft.screen.slot.CraftingResultSlot;
import net.minecraft.screen.slot.Slot;
import org.spongepowered.asm.mixin.Final;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/**
 * Mineflayer's bot.craft() can take the crafting result through Slot#takeStack
 * without consulting Slot#canTakeItems. Gate the result slot at the actual take.
 */
@Mixin(CraftingResultSlot.class)
public abstract class CraftingResultSlotTakeMixin {
    @Shadow @Final private PlayerEntity player;

    @Inject(method = "takeStack(I)Lnet/minecraft/item/ItemStack;",
            at = @At("HEAD"), cancellable = true)
    private void mcdrift$gateCraftResultTakeStack(int amount,
                                                  CallbackInfoReturnable<ItemStack> cir) {
        if (player.world == null || player.world.isClient()) return;
        GateConfig.maybeHotReload();
        Slot self = (Slot) (Object) this;
        if (!GateConfig.get().allowCraftTake(player, self.getStack())) {
            cir.setReturnValue(ItemStack.EMPTY);
        }
    }
}
