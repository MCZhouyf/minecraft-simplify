async function gatherDirt(bot) {
    bot.chat('Gathering dirt started');
    const dirtBlock = await exploreUntil(bot, new Vec3(1, -1, 1), 60, () => {
        const dirt = bot.findBlock({
            matching: mcData.blocksByName["dirt"].id,
            maxDistance: 32
        });
        return dirt;
    });
    if (!dirtBlock) {
        bot.chat("No dirt block found.");
        return;
    }
    await mineBlock(bot, "dirt", 16);
    bot.chat("Mined 16 dirt blocks.");
}