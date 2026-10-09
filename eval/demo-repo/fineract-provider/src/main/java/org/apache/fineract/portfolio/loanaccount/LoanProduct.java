package org.apache.fineract.portfolio.loanaccount;

/** Demo loan product terms. */
public class LoanProduct {

    public static double fetchRate() {
        return 0.0;
    }

    public static String describe() {
        return "standard installment loan";
    }

    public boolean isActive() {
        return true;
    }
}
