/** @odoo-module **/
import { Thread } from "@mail/core/common/thread_model";
import { patch } from "@web/core/utils/patch";
import { rpc } from "@web/core/network/rpc";

patch(Thread.prototype, {
    async post(body, postData = {}, extraData = {}) {
        const message = await super.post(body, postData, extraData);

        const aiMember = this.channel_member_ids?.find(
            (member) => member.partner_id?.im_status == "agent"
        );
        if (message?.thread?.ai_agent_id) {
            try {
                if (aiMember) {
                    aiMember.isTyping = true;
                }
                await rpc("/ai/get_ai_response", {
                    mail_message_id: message.id,
                    channel_id: this.id,
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