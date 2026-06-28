async function mineGoldOre(bot) {
    bot.chat('Gathering gold ore started');
    const ironPickaxeCount = bot.inventory.count(mcData.itemsByName.iron_pickaxe.id);
    const diamondPickaxeCount = bot.inventory.count(mcData.itemsByName.diamond_pickaxe.id);

    if (ironPickaxeCount < 1 && diamondPickaxeCount < 1) {
        bot.chat("No iron_pickaxe or diamond_pickaxe. Mining gold ore failed");
        return;
    }
    // Find a gold ore block. Stage-1 C2 can gate both vanilla and deepslate gold ore.
    const goldOreBlock = await exploreUntil(bot, new Vec3(0, -1, 0), 60, () => {
        return bot.findBlock({
            matching: [
                mcData.blocksByName["gold_ore"].id,
                mcData.blocksByName["deepslate_gold_ore"].id,
            ],
            maxDistance: 32
        });
    });
    if (!goldOreBlock) {
        bot.chat("No gold ore found.");
        return;
    }
    // Mine the gold ore block
    await mineBlock(bot, goldOreBlock.name, 1);
    bot.chat("Mined 1 gold ore.");
}
