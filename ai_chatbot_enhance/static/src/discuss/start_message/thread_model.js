/** @odoo-module **/

import { Thread } from "@mail/core/common/thread_model";
import { patch } from "@web/core/utils/patch";

patch(Thread.prototype, {
    update(data) {
        super.update(data);
        if ("ai_agent_id" in data) {
            this.ai_agent_id = data.ai_agent_id;
        }
    },

});