async function craftFence(bot) {
    const planksTypes = ["oak_planks", "birch_planks", "spruce_planks", "jungle_planks", "acacia_planks", "dark_oak_planks", "mangrove_planks"];
    const position = bot.entity.position.offset(1, 0, 0);
    await placeItem(bot, "crafting_table", position);

    for (let planksType of planksTypes) {
        let planks = bot.inventory.findInventoryItem(mcData.itemsByName[planksType].id);
        if (planks) {
            let fenceType = planksType.replace('_planks', '_fence');
            await craftItem(bot, fenceType, 1);
            bot.chat(`Crafted ${fenceType.replace('_', ' ')}.`);
            return;
        }
    }
}