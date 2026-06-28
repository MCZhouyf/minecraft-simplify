async function smeltRawIron(bot) {
    const rawIronCount = bot.inventory.count(mcData.itemsByName.raw_iron.id);

    if (rawIronCount < 1) {
        bot.chat("No enough raw iron. Smelting failed");
        return;
    }

    const nearbyFurnace = bot.findBlock({
        matching: mcData.blocksByName.furnace.id,
        maxDistance: 8,
    });
    const furnaceCount = bot.inventory.count(mcData.itemsByName.furnace.id);

    if (!nearbyFurnace && furnaceCount < 1) {
        bot.chat("No furnace. Smelting failed");
        return;
    }

    const fuelTypes = ["coal"];
    const logTypes = ["oak_log", "birch_log", "spruce_log", "jungle_log", "acacia_log", "dark_oak_log", "mangrove_log"];
    const plankTypes = logTypes.map(logType => logType.replace('_log', '_planks'));
    fuelTypes.push(...plankTypes);
    if (!nearbyFurnace) {
        const base = bot.entity.position.floored();
        let position = null;
        const candidateOffsets = [];
        for (let radius = 1; radius <= 4; radius++) {
            for (let dx = -radius; dx <= radius; dx++) {
                for (let dz = -radius; dz <= radius; dz++) {
                    if (Math.max(Math.abs(dx), Math.abs(dz)) !== radius) {
                        continue;
                    }
                    candidateOffsets.push([dx, -1, dz]);
                    candidateOffsets.push([dx, 0, dz]);
                    candidateOffsets.push([dx, -2, dz]);
                }
            }
        }

        for (const [dx, dy, dz] of candidateOffsets) {
            const groundPos = base.offset(dx, dy, dz);
            const placePos = groundPos.offset(0, 1, 0);
            const headPos = groundPos.offset(0, 2, 0);
            const groundBlock = bot.blockAt(groundPos);
            const placeBlock = bot.blockAt(placePos);
            const headBlock = bot.blockAt(headPos);
            if (!groundBlock || groundBlock.name === "air") {
                continue;
            }
            if (!placeBlock || placeBlock.name !== "air") {
                continue;
            }
            if (headBlock && headBlock.name !== "air") {
                continue;
            }
            position = placePos;
            bot.chat(`Selected furnace position ${position.x} ${position.y} ${position.z}`);
            break;
        }

        if (!position) {
            throw new Error(
                `No valid nearby air block found for furnace around ${base.x} ${base.y} ${base.z}`
            );
        }

        await placeItem(bot, "furnace", position);
        await bot.waitForTicks(5);
        const placedFurnace = bot.findBlock({
            matching: mcData.blocksByName.furnace.id,
            maxDistance: 8,
        });
        if (!placedFurnace) {
            throw new Error(
                `Failed to place furnace near ${position.x} ${position.y} ${position.z}`
            );
        }
    }

    for (let fuelType of fuelTypes) {
        let fuel = bot.inventory.findInventoryItem(mcData.itemsByName[fuelType].id);
        if (fuel) {
            await smeltItem(bot, "raw_iron", fuelType, 1);
            bot.save("raw_iron_smelted");
            bot.chat(`Smelted raw iron into iron ingot using ${fuelType.replace('_', ' ')}.`);
            return;
        }
    }
    throw new Error("No valid fuel found. Smelting failed");
}
