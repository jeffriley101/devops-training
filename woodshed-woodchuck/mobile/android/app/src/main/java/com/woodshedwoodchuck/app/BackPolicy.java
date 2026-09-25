package com.woodshedwoodchuck.app;

final class BackPolicy {
    // This is the sole native-to-page operation. No DOM inspection or JavaScript interface.
    static final String DISMISS_SCRIPT = "(function(){try{return !!(window.WWNavigation"
            + " && window.WWNavigation.dismissCurrent());}catch(e){return false;}})()";
    enum Action { STAY, HISTORY, EXIT }

    static Action afterDismiss(String result, boolean hasInternalHistory) {
        if ("true".equals(result)) return Action.STAY;
        return hasInternalHistory ? Action.HISTORY : Action.EXIT;
    }

    private BackPolicy() {}
}
