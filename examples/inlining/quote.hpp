#pragma once
struct Quote {
    double bid, ask;
    double spread_inline() const { return ask - bid; }   // defined in the header: inlinable
    double spread_outofline() const;                    // defined in quote.cpp: a real call
};
