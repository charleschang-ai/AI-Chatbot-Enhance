/** @odoo-module **/
import { Thread } from "@mail/core/common/thread_model";
import { patch } from "@web/core/utils/patch";
import { rpc } from "@web/core/network/rpc";

patch(Thread.prototype, {
    /**
     * 当用户发送消息后，如果当前频道是 AI 频道，则触发后端调用 DeepSeek
     */
    async post(body, postData = {}, extraData = {}) {
        const message = await super.post(body, postData, extraData);

        const aiMember = this.channel_member_ids?.find(
            (member) => member.partner_id?.im_status == "agent"
        );
        // message could be undefined if it is a command, for example /help.
        if (message?.thread?.ai_agent_id) {
            try {
                if (aiMember) {
                    aiMember.isTyping = true;
                }
                await rpc("/ai/get_ai_response", {
                    mail_message_id: message.id,
                    channel_id: this.id,
                    // current_view_info: await getCurrentViewInfo(this.store.env.bus),
                    // ai_session_identifier: session.ai_session_identifier,
                });
            } finally {
                if (aiMember) {
                    aiMember.isTyping = false;
                }
            }
        }
        return message;
    },
});