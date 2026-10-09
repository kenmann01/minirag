package org.apache.fineract.portfolio.loanaccount;

/** Demo repayment schedule. */
public class LoanSchedule {

    public static void activate() {
        // mark the schedule active
    }

    public static void deactivate() {
        // mark the schedule inactive
    }

    public static void start() {
        nextInstallment();
    }

    public static double recalculate() {
        double rate = LoanProduct.fetchRate();
        return rate;
    }

    public void defer() {
        this.nextInstallment();
        Client.notifyDeferment();
    }

    static void nextInstallment() {
        // advance the installment cursor
    }
}
