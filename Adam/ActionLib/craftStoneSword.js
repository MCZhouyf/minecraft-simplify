async function craftStoneSword(bot) {
    const position = bot.entity.position.offset(1, 0, 0);
    await placeItem(bot, "crafting_table", position);
    await craftItem(bot, "stone_sword", 1);
    bot.chat("Crafted a stone sword.");
}
