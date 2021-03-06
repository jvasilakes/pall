import numpy as np
import scipy.spatial.distance as spd

from collections import namedtuple


__all__ = ['UncertaintySampler',
           'CombinedSampler',
           'DistDivSampler',
           'Random',
           'SimpleMargin',
           'Margin',
           'Entropy',
           'LeastConfidence',
           'LeastConfidenceBias',
           'LeastConfidenceDynamicBias',
           'DistanceToCenter',
           'MinMax',
           'Density']


class QueryStrategy(object):
    '''
    Base query strategy class. In general, a query strategy
    consists of a scoring function, which assigns scores to
    each unlabeled instance, and a query function, which chooses
    and instance from these scores.
    '''
    def __init__(self):
        #  Unlabeled data, labeled data, classifier.
        self.Args = namedtuple('Args', ['U', 'L', 'clf'])

    def get_args(self, *args):
        '''
        Creates a namedtuple instance containing arguments to score().
            *args is:
        :param Data unlabeled: Unlabeled set.
        :param Data labeled: Labeled set.
        :param sklearn.base.BaseEstimator classifier: Classifier to use.
        :returns: Arguments
        :rtype: namedtuple
        '''
        if len(args) != 3:
            raise ValueError("Number of arguments must be 3")
        args = self.Args(U=args[0], L=args[1], clf=args[2])
        return args

    def score(self, *args, **kwargs):
        '''
        Computes an array of scores for members of U from which
        to choose.
        '''
        raise NotImplementedError()

    def choose(self, scores):
        '''
        Picks the most informative example according to its score.
        '''
        raise NotImplementedError()

    def query(self, *args, **kwargs):
        '''
        A simple interface to self.score() and self.choose().
        '''
        scores = self.score(*args, **kwargs)
        index = self.choose(scores)
        return index


class UncertaintySampler(QueryStrategy):

    def __init__(self, model_change=False):
        super().__init__()
        self.model_change = model_change
        if self.model_change is True:
            # These will be set of the wrapped score method.
            self.previous_scores = None
            self.chosen_index = None

    def __score(self):
        '''
        In uncertainty sampling it is possible to use model change,
        which is implemented as a wrapper around the scoring function.
        See model_change() below. The __init__() method for a child of
        this class should define the score() method in the following manner:

            if self.model_change is True:
                self.score = self.model_change_wrapper(self.__score)
            else:
                self.score = self.__score
        '''
        raise NotImplementedError()

    def model_change_wrapper(self, score_func):
        '''
        Model change wrapper around the scoring function. See doc
        for __score() above for usage insructions.

        :math:`score_{mc}(X) = score(X; t) - w_o score(X; t-1)`

        :math:`score(X, t)`: The score at time t

        :math:`w_o = \\frac{1}{\\mid L \\mid}`

        :param function score_func: Scoring function to wrap.
        :returns: Wrapped scoring function.
        :rtype: function
        '''
        def wrapper(*args):
            args = self.get_args(*args)
            scores = score_func(*args)
            w_o = 1 / args.L.y.shape[0]
            if self.chosen_index is None:  # i.e. this is the first run.
                self.previous_scores = np.zeros(shape=scores.shape)
            else:
                # If we've chosen and thus removed an example, we have to
                # remove it from the current self.previous_scores to make
                # sure we're comparing the same examples across iterations.
                self.previous_scores = np.delete(self.previous_scores,
                                                 self.chosen_index, axis=0)
            scores = scores - (w_o * self.previous_scores)
            self.previous_scores = scores  # Save these scores for next time.
            return scores
        return wrapper

    def choose(self, scores):
        '''
        :param numpy.ndarray scores: Output of self.score()
        :returns: Index of chosen example.
        :rtype: int
        '''
        return np.argmax(scores)


class CombinedSampler(QueryStrategy):
    '''
    Allows one sampler's scores to be weighted by anothers according
    to the equation:

    :math:`score(x) = score_{qs1}(x) \\times score_{qs2}(x)^{\\beta}`

    Assumes :math:`x^* = argmax(score)`
    '''
    # TODO: test if choice_metric matches choose() of qs1 and qs2.
    def __init__(self, qs1=None, qs2=None, beta=1, choice_metric=np.argmax):
        '''
        :param QueryStrategy qs1: Main query strategy.
        :param QueryStrategy qs2: Query strategy to use as weight.
        :param float beta: Scale factor for score_qs2.
        :param function choice_metric: Function that takes a 1d np.array
                                       and returns a chosen index.
        '''
        if qs1 is None or qs2 is None:
            raise ValueError("Must supply both qs1 and qs2")
        super().__init__()
        self.qs1 = qs1
        self.qs2 = qs2
        if beta == 'dynamic':
            self.beta = beta
        else:
            self.beta = float(beta)
        self.choice_metric = choice_metric
        self.U_0_size = None

    def __str__(self):
        return f"Combined Sampler: qs1: {self.qs1}; qs2 {self.qs2}; beta={self.beta}"  # noqa

    def __repr__(self):
        return "CombinedSampler"

    def _compute_beta(self, *args):
        '''
        Dynamic beta is computed according to the ratio of number of labeled
        to unlabeled samples.

        :math:`\beta = 2|U|/|L|`
        :returns: beta
        :rtype: float
        '''
        args = self.get_args(*args)
        beta = 2 * (args.U.X.shape[0] / args.L.X.shape[0])
        return beta

    def _normalize_scores(self, scores):
        '''
        Computes minmax normalization to map scores to the (0,1) interval.
        '''
        if all(scores == scores[0]):  # If all scores are equal.
            norm_scores = scores if scores[0] < 1 else (1/scores[0])
        else:
            num = scores - np.min(scores)
            denom = np.max(scores) - np.min(scores)
            norm_scores = num / denom
        return norm_scores

    def score(self, *args):
        '''
        Computes the combined scores from qs1 and qs2.
        :returns: scores
        :rtype: numpy.ndarray
        '''
        if self.beta == 'dynamic':
            beta = self._compute_beta(*args)
        else:
            beta = self.beta
        qs1_scores = self._normalize_scores(self.qs1.score(*args))
        qs2_scores = self._normalize_scores(self.qs2.score(*args))
        scores = qs1_scores * (qs2_scores**beta)
        return scores

    def choose(self, scores):
        '''
        Returns the example with the "best" score
        according to self.choice_metric.
        '''
        return self.choice_metric(scores)


# TODO: Write unit tests.
class DistDivSampler(QueryStrategy):
    '''
    Combined sampling method as in
    "Active learning for clinical text classification:
    is it better than random sampling?"

    :math:`x^* = argmin_x (\\lambda score_{qs1}(x) +
    (1 - \\lambda) score_{qs2}(x))`
    '''
    def __init__(self, qs1=None, qs2=None, lam=0.5, choice_metric=np.argmax):
        '''
        :param QueryStrategy qs1: Uncertainty sampling query strategy.
        :param QueryStrategy qs2: Representative sampling query strategy.
        :param float lambda: Query strategy weight [0,1] or "dynamic".
        :param function choice_metric: Function that takes a 1d np.array
                                       and returns a chosen index.
        '''
        if qs1 is None or qs2 is None:
            raise ValueError("Must supply both qs1 and qs2")
        super().__init__()
        self.qs1 = qs1
        self.qs2 = qs2
        if lam == "dynamic":
            self.lam = lam
        else:
            self.lam = float(lam)
        self.choice_metric = choice_metric

    def __str__(self):
        return f"DistDiv Sampler: qs1={self.qs1}; qs2={self.qs2}; lambda={self.lam}"  # noqa

    def __repr__(self):
        return "DistDivSampler"

    def _compute_lambda(self, *args):
        '''
        Dynamic lambda is computed according to the ratio of
        number of labeled to the total number of samples.

        lambda = |L| / (|L|+|U|)
        :returns: lambda
        :rtype: float
        '''
        args = self.get_args(*args)
        lam = args.L.X.shape[0] / (args.L.X.shape[0] + args.U.X.shape[0])
        a = 20000
        lam = (a**lam - 1) / (a - 1)  # Exponential decay
        return lam

    def _normalize_scores(self, scores):
        '''
        Computes minmax normalization to map scores to the (0,1) interval.
        '''
        if all(scores == scores[0]):  # If all scores are equal.
            norm_scores = scores if scores[0] < 1 else (1/scores[0])
        else:
            num = scores - np.min(scores)
            denom = np.max(scores) - np.min(scores)
            norm_scores = num / denom
        return norm_scores

    def score(self, *args):
        '''
        Computes the combined scores from qs1 and qs2.
        :returns: scores
        :rtype: numpy.ndarray
        '''
        if self.lam == "dynamic":
            lam = self._compute_lambda(*args)
        else:
            lam = self.lam
        qs1_scores = self._normalize_scores(self.qs1.score(*args))
        qs2_scores = self._normalize_scores(self.qs2.score(*args))
        scores = (lam * qs1_scores) + ((1 - lam) * qs2_scores)
        return scores

    def choose(self, scores):
        '''
        Returns the example with the "best" score
        according to self.choice_metric.
        '''
        return self.choice_metric(scores)


class Random(QueryStrategy):
    '''
    Random query strategy. Equivalent to passive learning.
    '''
    def __init__(self):
        super().__init__()

    def __str__(self):
        return "Random Sampler"

    def score(self, *args):
        '''
        In the random case, just output the indices.
        '''
        args = self.get_args(*args)
        return np.arange(args.U.X.shape[0])

    def choose(self, scores):
        '''
        Picks an index at random.
        :param numpy.ndarray scores: Output of self.score()
        :returns: Index of chosen example.
        :rtype: int
        '''
        return np.random.choice(scores)


class SimpleMargin(QueryStrategy):
    '''
    Finds the example x that is closest to the separating hyperplane.

    :math:`x^* = argmin_x |f(x)|`
    '''
    def __init__(self):
        super().__init__()

    def __str__(self):
        return "Simple Margin Sampler"

    def score(self, *args):
        '''
        Computes distances to the hyperplane for each member of
        the unlabeled set.
        '''
        args = self.get_args(*args)
        distances = args.clf.decision_function(args.U.X)
        scores = np.abs(distances)
        return scores

    def choose(self, scores):
        '''
        Returns the example with the shortest distance to the hyperplane.
        In the multiclass case, his will return the row index of the
        example with the smallest absolute distance to any hyperplane.
        Could be modified to choose the smallest average distance
        to all hyperplanes.
        :param numpy.ndarray scores: Output of self.score()
        :returns: Index of chosen example.
        :rtype: int
        '''
        if len(scores.shape) == 1:  # Binary classification returns a vector.
            scores = scores.reshape(-1, 1)
        idx = np.argmin(scores)
        row_idx = np.unravel_index(idx, scores.shape, order='C')[0]
        return row_idx


class Margin(QueryStrategy):
    '''
    Margin Sampler. Chooses the member from the unlabeled set
    with the smallest difference between the posterior probabilities
    of the two most probable class labels.

    :math:`x^* = argmin_x P(\\hat{y_1}|x) - P(\\hat{y_2}|x)`

        where :math:`\\hat{y_1}` is the most probable label
          and :math:`\\hat{y_2}` is the second most probable label.

    '''
    def __init__(self):
        super().__init__()

    def __str__(self):
        return "Margin Sampler"

    def score(self, *args):
        '''
        Computes the difference between posterior probability estimates
        for the top two most probable labels.
        :returns: Posterior probability differences.
        :rtype: numpy.ndarray
        '''
        args = self.get_args(*args)
        probs = args.clf.predict_proba(args.U.X)
        # Sort each row from high to low. Multiply by -1 to keep sign the same.
        probs = np.sort(-probs, axis=1) * -1
        # Compute the difference between first and second most likely labels.
        scores = probs[:, 0] - probs[:, 1]
        return scores

    def choose(self, scores):
        '''
        Returns the example with the smallest difference between the two
        most probable class labels.
        :param numpy.ndarray scores: Output of self.score()
        :returns: Index of chosen example.
        :rtype: int
        '''
        return np.argmin(scores)


class Entropy(UncertaintySampler):
    '''
    Entropy Sampler. Chooses the member from the unlabeled set
    with the greatest entropy across possible labels.

    :math:`x^* = argmax_x -\\sum_i P(y_i|x) \\times log_2(P(y_i|x))`
    '''
    def __init__(self, model_change=False):
        super().__init__(model_change=model_change)
        # Define self.score()
        if self.model_change is True:
            self.score = self.model_change_wrapper(self.__score)
        else:
            self.score = self.__score

    def __str__(self):
        return "Entropy Sampler"

    def __score(self, *args):
        '''
        Computes entropies for each member of the unlabeled set.
        :returns: Entropies.
        :rtype: numpy.ndarray
        '''
        args = self.get_args(*args)
        probs = args.clf.predict_proba(args.U.X)
        # TODO: Catch warning when 0 in probs and only display it once.
        # Can't handle 0 probabilities with log.
        if (probs.ravel() == 0).any():
            probs[probs == 0] = 1e-16
        scores = -np.sum(np.multiply(probs, np.log2(probs)), axis=1)
        return scores


class LeastConfidence(UncertaintySampler):
    '''
    Least confidence (uncertainty sampling). Chooses the member from
    the unlabeled set with the greatest uncertainty, i.e. the greatest
    posterior probability of all labels except the most likely one.

    :math:`x^* = argmax_x 1 - P(\\hat{y}|x)`

        where :math:`\\hat{y} = argmax_y P(y|x)`
    '''
    def __init__(self, model_change=False):
        super().__init__(model_change=model_change)
        # Define self.score()
        if self.model_change is True:
            self.score = self.model_change_wrapper(self.__score)
        else:
            self.score = self.__score

    def __str__(self):
        return "Least Confidence"

    def __score(self, *args):
        '''
        Computes leftover probabilities for each member of the unlabeled set.
        :returns: Leftover probabilities.
        :rtype: numpy.ndarray
        '''
        args = self.get_args(*args)
        probs = args.clf.predict_proba(args.U.X)
        scores = 1 - np.max(probs, axis=1)
        return scores


class LeastConfidenceBias(UncertaintySampler):
    '''
    Least confidence with bias. This is the same as least confidence, but
    moves the decision boundary according to the current class distribution.

    .. math::

        x^* =
        \\Biggl \\lbrace
        {
        \\frac{P(\\hat{y}|x)}{P_{max}}, \\text{ if } {P(\\hat{y}|x) < P_{max}}
        \\atop
        \\frac{1 - P(\\hat{y}|x)}{P_{max}}, \\text{ otherwise }
        }

    where

    :math:`P_{max} = mean(0.5, 1 - pp)` and
    :math:`pp` is the percentage of positive examples in the labeled set.
    '''
    def __init__(self, model_change=False):
        super().__init__(model_change)
        # Define self.score()
        if self.model_change is True:
            self.score = self.model_change_wrapper(self.__score)
        else:
            self.score = self.__score

    def __str__(self):
        return "Least Confidence with Bias"

    def __score(self, *args):
        '''
        Computes leftover probabilities for each member of the unlabeled set,
        adjusted for the current class distribution.
        :returns: scores
        :rtype: numpy.ndarray
        '''
        args = self.get_args(*args)
        pp = sum(args.L.y) / args.L.y.shape[0]
        p_max = np.mean([0.5, 1 - pp])
        probs = np.max(args.clf.predict_proba(args.U.X), axis=1)
        scores = np.where(probs < p_max,        # If
                          probs / p_max,        # Then
                          (1 - probs) / p_max)  # Else
        return scores


class LeastConfidenceDynamicBias(UncertaintySampler):
    '''
    Least confidence with dynamic bias. This is the same as least confidence
    with bias, but the bias also adjusts for the relative sizes of the
    labeled and unlabeled data sets.

    .. math::

        x^* =
        \\Biggl \\lbrace
        {
        \\frac{P(\\hat{y}|x)}{P_{max}}, \\text{ if } {P(\\hat{y}|x) < P_{max}}
        \\atop
        \\frac{1 - P(\\hat{y}|x)}{P_{max}}, \\text{ otherwise }
        }

    where

    :math:`P_{max} = (1 - pp)w_b + 0.5w_y`

    :math:`pp` is the percentage of positive examples in the labeled set.

    :math:`w_u = \\frac{|L|}{U_0}` and
    :math:`U_0` is the initial unlabeled set.

    :math:`w_b = 1 - w_u`
    '''
    def __init__(self, model_change=False):
        super().__init__(model_change)
        self.U_0_size = -1
        # Define self.score()
        if self.model_change is True:
            self.score = self.model_change_wrapper(self.__score)
        else:
            self.score = self.__score

    def __str__(self):
        return "Least Confidence with Dynamic Bias"

    def __score(self, *args):
        '''
        :returns: scores
        :rtype: numpy.ndarray
        '''
        args = self.get_args(*args)
        if self.U_0_size < 0:  # Set U_0_size if unset (-1)
            self.U_0_size = args.U.X.shape[0]
        pp = sum(args.L.y) / args.L.y.shape[0]
        w_u = args.L.y.shape[0] / self.U_0_size
        w_b = 1 - w_u
        p_max = w_b * (1 - pp) + w_u * 0.5
        probs = args.clf.predict_proba(args.U.X)[:, 1]
        scores = np.where(probs < p_max,        # If
                          probs / p_max,        # Then
                          (1 - probs) / p_max)  # Else
        return scores


class DistanceToCenter(QueryStrategy):
    '''
    Distance to Center sampling. Measures the distance of each point
    to the average x (center) in the labeled data set and computes
    the similarity using the equation below.

    :math:`x* = argmin_x \\frac{1}{1 + dist(x, x_L)}`

    where dist(A, B) is the distance between vectors A and B.

    :math:`x_L` is the mean vector in L (i.e. L's center).
    '''
    def __init__(self, metric='euclidean'):
        '''
        :param str metric: Distance metric to use. See spd.cdist doc for
                           available metrics.
        '''
        super().__init__()
        self.distance_metric = metric
        self.VI = None

    def __str__(self):
        return "Distance to Center Sampler"

    def score(self, *args):
        '''
        :returns: Distances.
        :rtype: numpy.ndarray
        '''
        args = self.get_args(*args)
        mean_labeled_x = np.mean(args.L.X, axis=0)
        if self.distance_metric == 'mahalanobis' and self.VI is None:
            full_matrix = np.vstack([args.U.X, args.L.X]).T
            # Use pseudo inverse because features are sparse.
            self.VI = np.linalg.pinv(np.cov(full_matrix)).T
        distances = spd.cdist([mean_labeled_x], args.U.X,
                              metric=self.distance_metric, VI=self.VI)
        densities = 1 / (1 + distances)
        return densities[0]

    def choose(self, scores):
        '''
        Returns the example with the lowest similarity to the average x in L.
        :param numpy.ndarray scores: Output of self.score()
        :returns: Index of chosen example.
        :rtype: int
        '''
        return np.argmin(scores)


class Density(QueryStrategy):
    '''
    Finds the example x in U that has the greatest average distance to
    every other point in U.

    :math:`x^* = argmin_x \\frac{1}{U} \\sum_{u=1} \\frac{1}{1 + dist(x, x_u)}`
    '''
    def __init__(self, metric='euclidean'):
        '''
        :param str metric: Distance metric to use. See spd.cdist doc for
                           available metrics.
        '''
        super().__init__()
        self.distance_metric = metric
        self.VI = None

    def __str__(self):
        return "Density Sampler"

    def score(self, *args):
        '''
        Computes average distance between each member of U and each other
        member of U.
        :returns: Minimum distances from each point in U to each other point.
        :rtype: numpy.ndarray
        '''
        args = self.get_args(*args)
        # Computing similarity to itself will fail.
        if args.U.X.shape[0] == 1:
            return np.empty(1)
        if self.distance_metric == 'mahalanobis' and self.VI is None:
            full_matrix = np.vstack([args.U.X, args.L.X]).T
            # Use pseudo inverse because features are sparse.
            self.VI = np.linalg.pinv(np.cov(full_matrix)).T
        distances = spd.cdist(args.U.X, args.U.X,
                              metric=self.distance_metric, VI=self.VI)
        if np.isnan(distances).any():
            raise ValueError("Distances contain NaN values. Check that input vectors != 0.")  # noqa
        num_x = args.U.X.shape[0]
        # Remove zero scores b/c we want distance from every OTHER point.
        np.fill_diagonal(distances, np.NaN)
        distances = distances[~np.isnan(distances)].reshape(num_x, num_x - 1)
        similarities = 1 / (1 + distances)
        scores = np.mean(similarities, axis=1)
        return scores

    def choose(self, scores):
        '''
        Returns the example with the lowest similarity to the average x in U.
        :param numpy.ndarray scores: Output of self.score()
        :returns: Index of chosen example.
        :rtype: int
        '''
        return np.argmin(scores)


class MinMax(QueryStrategy):
    '''
    Finds the exmaple x in U that has the maximum smallest distance
    to every point in L. Ensures representative coverage of the dataset.

    :math:`x^* = argmax_{x_i} ( min_{x_j} dist(x_i, x_j) )`

    where :math:`x_i \\in U`, :math:`x_j \\in L`, dist(.) is the
    given distance metric.
    '''
    def __init__(self, metric='euclidean'):
        '''
        :param str metric: Distance metric to use. See the spd.cdist doc for
                           available metrics.
        '''
        super().__init__()
        self.distance_metric = str(metric)
        self.VI = None

    def __str__(self):
        return "Min Max Sampler"

    # TODO: Precompute distances to avoid redundant computation.
    def score(self, *args):
        '''
        Computes minimum distance between each member of unlabeled_x
            and each member of labeled_x.
        :returns: Minimum distances from each unlabeled_x to each labeled_x.
        :rtype: numpy.ndarray
        '''
        args = self.get_args(*args)
        if self.distance_metric == 'mahalanobis' and self.VI is None:
            full_matrix = np.vstack([args.U.X, args.L.X]).T
            # Use pseudo inverse because features are sparse.
            self.VI = np.linalg.pinv(np.cov(full_matrix)).T
        distances = spd.cdist(args.U.X, args.L.X,
                              metric=self.distance_metric, VI=self.VI)
        scores = np.min(distances, axis=1)
        return scores

    def choose(self, scores):
        '''
        Returns the examples with the greatest minimum distance to
        every other x in L.
        :param numpy.ndarray scores: Output of self.score()
        :returns: Index of chosen example.
        :rtype: int
        '''
        return np.argmax(scores)
