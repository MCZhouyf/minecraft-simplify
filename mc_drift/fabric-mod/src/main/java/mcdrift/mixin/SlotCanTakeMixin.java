package mcdrift.mixin;

import mcdrift.GateConfig;
import net.minecraft.entity.player.PlayerEntity;
import net.minecraft.screen.slot.CraftingResultSlot;
import net.minecraft.screen.slot.Slot;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/**
 * craft_result gates (P1, E2): refuse to hand over the crafting RESULT when the
 * gate condition is unmet. Server-side path of every take (PICKUP and QUICK_MOVE
 * both consult canTakeItems), so mineflayer's bot.craft() ends with no output —
 * exactly the INV-2 "no output" failure semantics. Covers both the crafting
 * table and the 2x2 inventory grid (both use CraftingResultSlot).
 *
 * Yarn 1.19 mapping: net.minecraft.screen.slot.Slot#canTakeItems(PlayerEntity)Z.
 * If the compiler cannot find this method after `gradlew genSources`, check the
 * generated Slot source for the renamed equivalent and update `method =` below.
 */
@Mixin(Slot.class)
public abstract class SlotCanTakeMixin {

    @Inject(method = "canTakeItems(Lnet/minecraft/entity/player/PlayerEntity;)Z",
            at = @At("HEAD"), cancellable = true)
    private void mcdrift$gateCraftResultTake(PlayerEntity player,
                                             CallbackInfoReturnable<Boolean> cir) {
        Slot self = (Slot) (Object) this;
        if (!(self instanceof CraftingResultSlot)) return;
        if (player.world == null || player.world.isClient()) return;
        GateConfig.maybeHotReload();
        if (!GateConfig.get().allowCraftTake(player, self.getStack())) {
            cir.setReturnValue(false);
        }
    }
}
