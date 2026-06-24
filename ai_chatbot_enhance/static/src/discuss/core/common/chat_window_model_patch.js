/** @odoo-module **/

import { jsonrpc } from "@web/core/network/rpc_service";
import { patch } from "@web/core/utils/patch";
import { ChatWindow } from "@mail/core/common/chat_window_model";

patch(ChatWindow.prototype, {
    computeCanShow() {
        if (this.store.aiInsertButtonTarget && this.store.discuss.isActive) {
            return this.thread?.channel_type === "ai_chat";
        }
        return super.computeCanShow();
    },
    async _onClose(options) {
        const thread = this.thread;
        if (thread?.ai_agent_id) {
            await jsonrpc("/ai/close_chat_ai", { channel_id: thread.id });
        }
        // await super._onClose(options);
    },
});
