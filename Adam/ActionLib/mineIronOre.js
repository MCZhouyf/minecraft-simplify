async function mineIronOre(bot) {
    bot.chat('Gathering iron ore started');
    const stonePickaxeCount = bot.inventory.count(mcData.itemsByName.stone_pickaxe.id);
    const ironPickaxeCount = bot.inventory.count(mcData.itemsByName.iron_pickaxe.id);

    if (stonePickaxeCount < 1 && ironPickaxeCount < 1) {
        bot.chat("No stone_pickaxe or iron_pickaxe. Mining iron ore failed");
        return;
    }
    // Find an iron ore block
    const ironOreBlock = await exploreUntil(bot, new Vec3(0, -1, 0), 60, () => {
        return bot.findBlock({
            matching: [
                mcData.blocksByName["iron_ore"].id,
                mcData.blocksByName["deepslate_iron_ore"].id,
            ],
            maxDistance: 32
        });
    });
    if (!ironOreBlock) {
        bot.chat("No iron ore found.");
        return;
    }
    // Mine the iron ore block
    await mineBlock(bot, ironOreBlock.name, 1);
    bot.chat("Mined 1 iron ore.");
}
