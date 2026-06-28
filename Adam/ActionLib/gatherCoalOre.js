async function mineCoalOre(bot) {
    bot.chat('Gathering coal ore started');
    const woodenPickaxeCount = bot.inventory.count(mcData.itemsByName.wooden_pickaxe.id);
    const stonePickaxeCount = bot.inventory.count(mcData.itemsByName.stone_pickaxe.id);

    if (woodenPickaxeCount < 1 && stonePickaxeCount < 1) {
        bot.chat("No wooden_pickaxe or stone_pickaxe. Mining coal ore failed");
        return;
    }
    // Find an coal ore block
    const coalOreBlock = await exploreUntil(bot, new Vec3(0, -1, 0), 60, () => {
        return bot.findBlock({
            matching: [
                mcData.blocksByName["coal_ore"].id,
                mcData.blocksByName["deepslate_coal_ore"].id,
            ],
            maxDistance: 32
        });
    });
    if (!coalOreBlock) {
        bot.chat("No coal ore found.");
        return;
    }
    // Mine the coal ore block
    await mineBlock(bot, coalOreBlock.name, 1);
    bot.chat("Mined 1 coal ore.");
}
