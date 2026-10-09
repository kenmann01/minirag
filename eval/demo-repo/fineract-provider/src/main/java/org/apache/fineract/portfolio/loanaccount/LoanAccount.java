package org.apache.fineract.portfolio.loanaccount;

/** Demo loans package for the gate and score rig. Package-private style. */
public class LoanAccount {

    public void approve() {
        LoanSchedule.activate();
        Client.notifyApproval();
    }

    public void disburse() {
        LoanSchedule.start();
        Client.openAccount();
    }

    public double accrueInterest() {
        return LoanSchedule.recalculate();
    }

    public void applyPenalty() {
        double rate = LoanProduct.fetchRate();
        recordPenalty(rate);
    }

    public void close() {
        Collateral.release();
        LoanSchedule.deactivate();
    }

    void recordPenalty(double rate) {
        // penalty bookkeeping
    }
}
