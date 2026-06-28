async function moveUp(bot) {
    const delay = (ms) => new Promise(resolve => setTimeout(resolve, ms));
    const mcData = require('minecraft-data')(bot.version);
    const defaultMove = new Movements(bot, mcData);
    bot.pathfinder.setMovements(defaultMove);

    let pos = bot.entity.position;
    let yaw = bot.entity.yaw;

    await bot.pathfinder.setGoal(new GoalBlock(pos.x, pos.y -20, pos.z));
    await delay(5000);
}