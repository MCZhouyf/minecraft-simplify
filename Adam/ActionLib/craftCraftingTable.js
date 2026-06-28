async function craftCraftingTable(bot) {
    bot.chat("Start crafting Crafting Table");

    const plankTypes = ["oak_planks", "birch_planks", "spruce_planks", "jungle_planks", "acacia_planks", "dark_oak_planks", "mangrove_planks"];
    let totalPlanks = 0;
    let planksToToss = [];

    for (let plankType of plankTypes) {
        let plank = bot.inventory.findInventoryItem(mcData.itemsByName[plankType].id);
        if (plank) {
            totalPlanks += bot.inventory.count(mcData.itemsByName[plankType].id);
            planksToToss.push(plankType);
        }
    }

    if (totalPlanks < 4) {
        bot.chat("Not enough planks to craft a crafting table.");
        return;
    }

    let planksDiscarded = 0;
    for (let plankType of planksToToss) {
        if (planksDiscarded >= 4) break;
        let toToss = Math.min(bot.inventory.count(mcData.itemsByName[plankType].id), 4 - planksDiscarded);
        await bot.toss(mcData.itemsByName[plankType].id, null, toToss);
        planksDiscarded += toToss;
    }

    bot.chat("/give @s crafting_table");
    bot.chat("Crafted a crafting_table");
}