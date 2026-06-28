async function moveLeft(bot) {
    const delay = (ms) => new Promise(resolve => setTimeout(resolve, ms));
    const mcData = require('minecraft-data')(bot.version);
    const defaultMove = new Movements(bot, mcData);
    bot.pathfinder.setMovements(defaultMove);

    let pos = bot.entity.position;
    let yaw = bot.entity.yaw;

    let newX = pos.x - 10 * Math.cos(yaw);
    let newZ = pos.z + 10 * Math.sin(yaw);

    await bot.chat(`Old Coordinates: (${pos.x.toFixed(2)}, ${pos.z.toFixed(2)}), Yaw: ${yaw}, New Coordinates: (${newX.toFixed(2)}, ${newZ.toFixed(2)})`);
    await bot.pathfinder.setGoal(new GoalXZ(newX, newZ));
    await delay(5000);
}