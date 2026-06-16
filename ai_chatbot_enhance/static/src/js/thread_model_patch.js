/** @odoo-module **/

import {ThreadService} from "@mail/core/common/thread_service";
import {patch} from "@web/core/utils/patch";
import {jsonrpc} from "@web/core/network/rpc_service";
import { OutOfFocusService } from "@mail/core/common/out_of_focus_service";

patch(ThreadService.prototype, {
    async post(thread, body, options = {}) {
        const message = await super.post(thread, body, options);

        const aiMember = thread.channel_member_ids?.find(
            (member) => member.partner_id?.im_status === "agent"
        );
        // ★ 用 thread.type，不是 ai_agent_id / channel_type
        if (thread?.type === "ai_chat") {
            try {
                if (aiMember) {
                    aiMember.isTyping = true;
                }
                await jsonrpc("/ai/get_ai_response", {
                    mail_message_id: message.id,
                    channel_id: thread.id,
                });
            } finally {
                if (aiMember) {
                    aiMember.isTyping = false;
                }
            }
        }
        return message;
    },

    async notify(message, channel) {
        // AI 频道不弹右上角新消息提示，也不响提示音
        if (channel?.type === "ai_chat") {
            return;
        }
        return super.notify(message, channel);
    },

});

patch(OutOfFocusService.prototype, {
    async notify(message, channel) {
        if (channel?.type === "ai_chat") {
            return;
        }
        return super.notify(message, channel);
    },
});