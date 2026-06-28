async function gatherStone(bot) {
    bot.chat('Gathering stone started');
    const stoneBlock = await exploreUntil(bot, new Vec3(1, -1, 1), 60, () => {
        const stone = bot.findBlock({
            matching: mcData.blocksByName["stone"].id,
            maxDistance: 32
        });
        return stone;
    });
    if (!stoneBlock) {
        bot.chat("No stone block found.");
        return;
    }
    await mineBlock(bot, "stone", 16);
    bot.chat("Mined 16 stone blocks.");
}