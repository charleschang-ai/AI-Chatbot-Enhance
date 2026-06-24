/** @odoo-module **/

import { registry } from "@web/core/registry";

async function initChat(env, action) {
    const threadService = env.services["mail.thread"];

    // 17 没有 getOrFetch：用 fetchChannel 拉频道（走 /discuss/channel/info 并 insert）
    const thread = await threadService.fetchChannel(Number(action.params.channelId));
    if (!thread) {
        throw new Error("Thread not found");
    }
    // 17 没有 thread.open()：用 threadService.open（discuss.channel 会开成聊天窗口）
    threadService.open(thread);
    await thread.isLoadedDeferred;
    if (action.params.user_prompt && thread.status !== "loading") {
        // 17 没有 thread.post()：用 threadService.post(thread, body)
        await threadService.post(thread, action.params.user_prompt);
    }
}

registry.category("actions").add("agent_chat_action", initChat);