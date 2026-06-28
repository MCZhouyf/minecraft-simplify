async function craftFurnace(bot) {
    const position = bot.entity.position.offset(1, 0, 0);
    await placeItem(bot, "crafting_table", position);
    await craftItem(bot, "furnace", 1);
    bot.chat("Crafted a furnace");
}
