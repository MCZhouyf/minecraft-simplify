async function craftPlanks(bot) {
    const logTypes = ["oak_log", "birch_log", "spruce_log", "jungle_log", "acacia_log", "dark_oak_log", "mangrove_log"];
    for (let logType of logTypes) {
        let log = bot.inventory.count(mcData.itemsByName[logType].id);
        if (log) {
            let plankType = logType.replace('_log', '_planks');
            await craftItem(bot, plankType, 10);
            bot.chat(`Crafted ${plankType.replace('_', ' ')}.`);
        }
    }
}
