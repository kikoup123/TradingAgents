import Foundation

struct StructuralPivotSet: Sendable {
    let highs: [CSDPivotReference]
    let lows: [CSDPivotReference]
}

struct StructuralPivotEngine: Sendable {
    let pivotSpan: Int

    init(pivotSpan: Int = 2) {
        self.pivotSpan = pivotSpan
    }

    func confirmedPivots(_ bars: [MarketCandle]) -> StructuralPivotSet {
        guard pivotSpan >= 1, bars.count >= 2 * pivotSpan + 1 else {
            return StructuralPivotSet(highs: [], lows: [])
        }
        let data = bars.sorted { $0.openTime < $1.openTime }
        var highs: [CSDPivotReference] = []
        var lows: [CSDPivotReference] = []

        for position in pivotSpan..<(data.count - pivotSpan) {
            let row = data[position]
            let left = data[(position - pivotSpan)..<position]
            let right = data[(position + 1)...(position + pivotSpan)]
            let confirmationPosition = position + pivotSpan

            if row.high > left.map(\.high).max()! && row.high > right.map(\.high).max()! {
                highs.append(
                    CSDPivotReference(
                        side: "HIGH",
                        price: row.high,
                        sourcePosition: position,
                        sourceTime: row.openTime,
                        confirmedPosition: confirmationPosition,
                        confirmedTime: data[confirmationPosition].openTime
                    )
                )
            }
            if row.low < left.map(\.low).min()! && row.low < right.map(\.low).min()! {
                lows.append(
                    CSDPivotReference(
                        side: "LOW",
                        price: row.low,
                        sourcePosition: position,
                        sourceTime: row.openTime,
                        confirmedPosition: confirmationPosition,
                        confirmedTime: data[confirmationPosition].openTime
                    )
                )
            }
        }
        return StructuralPivotSet(highs: highs, lows: lows)
    }
}
