async function mineBlock(bot, name, count = 1, opts = {}) {
    // return if name is not string
    if (typeof name !== "string") {
        throw new Error(`name for mineBlock must be a string`);
    }
    if (typeof count !== "number") {
        throw new Error(`count for mineBlock must be a number`);
    }
    const blockByName = mcData.blocksByName[name];
    if (!blockByName) {
        throw new Error(`No block named ${name}`);
    }
    const expectedItemName = opts.drop || (
        Array.isArray(blockByName.drops) && blockByName.drops.length === 1 && mcData.items[blockByName.drops[0]]
            ? mcData.items[blockByName.drops[0]].name
            : name
    );
    const expectedItem = mcData.itemsByName[expectedItemName];
    const beforeCount = expectedItem ? bot.inventory.count(expectedItem.id) : 0;
    const blocks = bot.findBlocks({
        matching: [blockByName.id],
        maxDistance: 32,
        count: 1024,
    });
    if (blocks.length === 0) {
        bot.chat(`No ${name} nearby, please explore first`);
        _mineBlockFailCount++;
        if (_mineBlockFailCount > 10) {
            throw new Error(
                "mineBlock failed too many times, make sure you explore before calling mineBlock"
            );
        }
        return;
    }
    const targets = blocks
        .map((pos) => bot.blockAt(pos))
        .filter(Boolean)
        .sort((a, b) => {
            return bot.entity.position.distanceTo(a.position) - bot.entity.position.distanceTo(b.position);
        })
        .slice(0, Math.max(count, 1));

    let mined = 0;
    const maxCollectAttempts = Number.isFinite(opts.maxCollectAttempts)
        ? Math.max(0, Math.floor(opts.maxCollectAttempts))
        : 16;
    const totalTimeoutMs = Number.isFinite(opts.totalTimeoutMs)
        ? Math.max(1000, Math.floor(opts.totalTimeoutMs))
        : Math.max(30000, Math.floor(count) * 30000);
    const deadline = Date.now() + totalTimeoutMs;

    function checkDeadline() {
        if (Date.now() > deadline) {
            throw new Error(`mineBlock timed out while mining ${name}`);
        }
    }

    async function gotoNear(position, range, timeoutMs = 4000) {
        checkDeadline();
        await Promise.race([
            bot.pathfinder.goto(new GoalNear(position.x, position.y, position.z, range)),
            new Promise((_, reject) => setTimeout(() => reject(new Error("pathfinder timeout")), timeoutMs)),
        ]).catch((err) => {
            bot.pathfinder.setGoal(null);
            throw err;
        });
    }

    for (const target of targets) {
        try {
            checkDeadline();
            const heldName = bot.heldItem?.name || "";
            const heldIsTool = /_(pickaxe|axe|shovel|hoe|sword)$/.test(heldName);
            if (!heldIsTool && typeof bot.tool?.equipForBlock === "function") {
                await bot.tool.equipForBlock(target);
            }
            if (bot.entity.position.distanceTo(target.position.offset(0.5, 0.5, 0.5)) > 4) {
                await gotoNear(target.position, 1);
            }
            await Promise.race([
                bot.dig(target),
                new Promise((_, reject) => setTimeout(() => reject(new Error(`Timed out digging ${name}`)), 15000)),
            ]);
            await bot.waitForTicks(2);
            const afterBlock = bot.blockAt(target.position);
            if (afterBlock && afterBlock.type === target.type) {
                bot.chat(`Could not mine ${target.name} at ${target.position}: block remained after dig`);
                return;
            }
            mined++;
            for (let i = 0; i < maxCollectAttempts; i++) {
                checkDeadline();
                await bot.waitForTicks(5);
                const itemEntity = bot.nearestEntity((entity) => {
                    return entity.name === "item" && entity.position.distanceTo(target.position.offset(0.5, 0.5, 0.5)) <= 6;
                });
                const pickupPos = itemEntity ? itemEntity.position : target.position;
                try {
                    await gotoNear(pickupPos, 0.25, 2500);
                } catch (err) {
                    bot.chat(`Could not walk to ${name} drops: ${err.message}`);
                }
                await bot.waitForTicks(5);
            }
        } catch (err) {
            bot.chat(`Could not mine ${target.name} at ${target.position}: ${err.message}`);
        }
    }

    if (mined > 0) {
        await bot.waitForTicks(6);
        if (expectedItem) {
            const gained = bot.inventory.count(expectedItem.id) - beforeCount;
            if (gained < Math.min(count, mined)) {
                throw new Error(`mined ${name} but collected only ${gained}/${Math.min(count, mined)} ${expectedItemName}`);
            }
        }
        bot.save(`${name}_mined`);
    }
}
