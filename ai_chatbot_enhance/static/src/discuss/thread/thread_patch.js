/** @odoo-module **/

import { Thread } from "@mail/core/common/thread";
import { patch } from "@web/core/utils/patch";

patch(Thread.prototype, {
    onClickPromptButton(button) {
        this.props.thread.post(button);
    },
    get showPromptButtons() {
        return this.props.thread.messages.length === 0;
    },
});
