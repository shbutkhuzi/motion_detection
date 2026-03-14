class RunningMetrics:
    def __init__(self):
        self.tp = 0
        self.fp = 0
        self.fn = 0
        self.tn = 0

    def update(self, y_true, y_pred):
        """Update counts based on a single incoming pair."""
        if y_true == 1 and y_pred == 1:
            self.tp += 1
        elif y_true == 0 and y_pred == 1:
            self.fp += 1
        elif y_true == 1 and y_pred == 0:
            self.fn += 1
        elif y_true == 0 and y_pred == 0:
            self.tn += 1
        

    def get_f1_score(self):
        """Calculate and return the current F1 score."""
        # Calculate Precision and Recall
        precision = self.get_precision()
        recall = self.get_recall()
        
        # Calculate F1
        if precision + recall == 0:
            return 0.0
        
        f1 = 2 * (precision * recall) / (precision + recall)
        return f1
    
    def get_accuracy(self):
        """Calculate and return the current accuracy."""
        return (self.tp + self.tn) / (self.tp + self.fp + self.fn + self.tn)
    
    def get_precision(self):
        """Calculate and return the current precision."""
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) > 0 else 0
    
    def get_recall(self):
        """Calculate and return the current recall."""
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) > 0 else 0
