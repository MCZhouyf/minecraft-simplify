async function gatherSand(bot) {
    bot.chat('Gathering sand started');
    const sandBlock = await exploreUntil(bot, new Vec3(1, -1, 1), 60, () => {
        const sand = bot.findBlock({
            matching: mcData.blocksByName["sand"].id,
            maxDistance: 32
        });
        return sand;
    });
    if (!sandBlock) {
        bot.chat("No sand block found.");
        return;
    }
    await mineBlock(bot, "sand", 16);
    bot.chat("Mined 16 sand blocks.");
}