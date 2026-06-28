async function mineDiamondOre(bot) {
    bot.chat('Mining diamond ore started');
    const ironPickaxeCount = bot.inventory.count(mcData.itemsByName.iron_pickaxe.id);

    if (ironPickaxeCount < 1) {
        bot.chat("No iron_pickaxe. Mining diamond ore failed");
        return;
    }

    // Find a diamond ore block
    const diamondOreBlock = await exploreUntil(bot, new Vec3(0, -1, 0), 20, () => {
        const diamondOre = bot.findBlock({
            matching: [
                mcData.blocksByName["diamond_ore"].id,
                mcData.blocksByName["deepslate_diamond_ore"].id,
            ],
            maxDistance: 32
        });
        return diamondOre;
    });

    if (!diamondOreBlock) {
        bot.chat("No diamond ore found.");
        return;
    }
    // Mine the diamond ore block
    await mineBlock(bot, diamondOreBlock.name, 1);
    bot.chat("Mined 1 diamond ore.");
}
