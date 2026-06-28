async function smeltRawGold(bot) {
    const rawGoldCount = bot.inventory.count(mcData.itemsByName.raw_gold.id);

    if (rawGoldCount < 1) {
        bot.chat("No enough raw gold. Smelting failed");
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
        const position = bot.entity.position.offset(1, 0, 0);
        await placeItem(bot, "furnace", position);
    }

    for (let fuelType of fuelTypes) {
        let fuel = bot.inventory.findInventoryItem(mcData.itemsByName[fuelType].id);
        if (fuel) {
            await smeltItem(bot, "raw_gold", fuelType, 1);
            bot.chat(`Smelted raw gold into gold ingot using ${fuelType.replace('_', ' ')}.`);
            return;
        }
    }
    throw new Error("No valid fuel found. Smelting failed");
}
