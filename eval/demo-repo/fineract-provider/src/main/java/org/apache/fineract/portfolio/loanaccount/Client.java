package org.apache.fineract.portfolio.loanaccount;

/** Demo client record. */
public class Client {

    public static void notifyApproval() {
        // approval notice
    }

    public static void notifyWithdrawal() {
        // withdrawal notice
    }

    public static void notifyDeferment() {
        // deferment notice
    }

    public static void openAccount() {
        // statement account setup
    }

    public boolean verifyIdentity() {
        updateAuditTrail();
        return true;
    }

    void updateAuditTrail() {
        // audit bookkeeping
    }
}
