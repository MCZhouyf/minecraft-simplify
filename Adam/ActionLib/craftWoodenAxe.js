async function craftWoodenAxe(bot) {
    const position = bot.entity.position.offset(1, 0, 0);
    await placeItem(bot, "crafting_table", position);
    await craftItem(bot, "wooden_axe", 1);
    bot.chat("Crafted a wooden axe.");
}
