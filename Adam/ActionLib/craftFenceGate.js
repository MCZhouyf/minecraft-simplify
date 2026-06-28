async function craftFenceGate(bot) {
    const planksTypes = ["oak_planks", "birch_planks", "spruce_planks", "jungle_planks", "acacia_planks", "dark_oak_planks", "mangrove_planks"];
    const position = bot.entity.position.offset(1, 0, 0);
    await placeItem(bot, "crafting_table", position);

    for (let planksType of planksTypes) {
        let planks = bot.inventory.findInventoryItem(mcData.itemsByName[planksType].id);
        if (planks) {
            let fence_gateType = planksType.replace('_planks', '_fence_gate');
            await craftItem(bot, fence_gateType, 1);
            bot.chat(`Crafted ${fence_gateType.replace('_', ' ')}.`);
            return;
        }
    }
}