package mcdrift.mixin;

import mcdrift.GateConfig;
import net.minecraft.block.BlockState;
import net.minecraft.block.entity.AbstractFurnaceBlockEntity;
import net.minecraft.util.math.BlockPos;
import net.minecraft.world.World;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * furnace_tick gates (P2, E1): freeze smelting/burning progress while the gate
 * condition is unmet (cancelling the whole tick stops both cookTime and burnTime,
 * so smeltItem simply never produces output — INV-2 "no output" semantics).
 *
 * Yarn 1.19 mapping: AbstractFurnaceBlockEntity#tick(World, BlockPos, BlockState,
 * AbstractFurnaceBlockEntity) — the static tick referenced from FurnaceBlock's
 * ticker. If `gradlew build` cannot resolve it, run `gradlew genSources`, open
 * the generated AbstractFurnaceBlockEntity and update the `method =` signature.
 */
@Mixin(AbstractFurnaceBlockEntity.class)
public abstract class AbstractFurnaceBlockEntityMixin {

    @Inject(method = "tick(Lnet/minecraft/world/World;Lnet/minecraft/util/math/BlockPos;"
                   + "Lnet/minecraft/block/BlockState;"
                   + "Lnet/minecraft/block/entity/AbstractFurnaceBlockEntity;)V",
            at = @At("HEAD"), cancellable = true)
    private static void mcdrift$gateFurnaceTick(World world, BlockPos pos, BlockState state,
                                                AbstractFurnaceBlockEntity blockEntity,
                                                CallbackInfo ci) {
        if (world.isClient()) return;
        if (!GateConfig.get().allowFurnaceTick(world, pos, blockEntity)) {
            ci.cancel();
        }
    }
}
