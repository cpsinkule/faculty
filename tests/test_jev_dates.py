"""JEV DATES (requirement, 2026-10-08): the dates and times a message names are read in code; a record's days are matched here."""
import unittest

from jev import dates, send_checks

SENT = '2026-10-07T15:00:00-05:00'  # a Wednesday, campus time


def said(text, sent=SENT):
    return [(m['words'], m['dates'], m['form']) for m in dates.mentions(text, sent)]


class MentionTests(unittest.TestCase):
    def test_written_dates_and_words_for_days_are_definite(self):
        self.assertEqual(said('Monday, October 5 and 10/9, also 2026-10-12'), [
            ('Monday, October 5', ['2026-10-05'], 'definite'), ('10/9', ['2026-10-09'], 'definite'),
            ('2026-10-12', ['2026-10-12'], 'definite')])
        self.assertEqual(said('I was sick yesterday and today; better tomorrow'), [
            ('yesterday', ['2026-10-06'], 'definite'), ('today', ['2026-10-07'], 'definite'), ('tomorrow', ['2026-10-08'], 'definite')])
        self.assertEqual(said('last night and this morning'), [('last night', ['2026-10-06'], 'definite'),
                                                               ('this morning', ['2026-10-07'], 'definite')])
        self.assertEqual(said('this Friday, this past Tuesday'), [('this Friday', ['2026-10-09'], 'definite'),
                                                                  ('this past Tuesday', ['2026-10-06'], 'definite')])

    def test_this_weekday_already_past_in_the_sent_week_is_a_bare_weekday(self):
        # 2026-10-09 review: "this Monday" written on a weekend named the Monday six days before; send checks already held a
        # past "this Monday" unclear, so now both readers agree: definite only on or before that day of the week.
        monday = ('this Monday', ['2026-10-05', '2026-10-12'], 'weekday')
        for sent in ('2026-10-07T15:00:00-05:00', '2026-10-10T10:00:00-05:00', '2026-10-11T10:00:00-05:00'):  # Wed, Sat, Sun
            with self.subTest(sent=sent):
                self.assertEqual(said('I will miss class this Monday for a doctor appointment.', sent), [monday])
                self.assertEqual(send_checks.mentions('I will miss class this Monday.', dates.campus_day(sent)), [])
        self.assertEqual(said('this Monday', '2026-10-05T10:00:00-05:00'), [('this Monday', ['2026-10-05'], 'definite')])  # Mon
        self.assertEqual(said('this Sunday', '2026-10-10T10:00:00-05:00'), [('this Sunday', ['2026-10-11'], 'definite')])
        found = dates.mentions('I will miss class this Monday.', '2026-10-11T10:00:00-05:00')
        self.assertEqual(dates.match('2026-10-12', found), {'kind': 'weekday', 'mention': found[0], 'side': 'later'})

    def test_a_bare_weekday_is_the_one_on_or_before_the_sent_day_then_the_next_and_last_or_next_stay_two_weeks(self):
        self.assertEqual(said('I missed class Monday'), [('Monday', ['2026-10-05', '2026-10-12'], 'weekday')])
        self.assertEqual(said('Wednesday'), [('Wednesday', ['2026-10-07', '2026-10-14'], 'weekday')])  # the sent day itself first
        self.assertEqual(said('next Monday'), [('next Monday', ['2026-10-12', '2026-10-19'], 'last_next')])
        self.assertEqual(said('last Friday'), [('last Friday', ['2026-09-25', '2026-10-02'], 'last_next')])
        self.assertEqual(said('on Mondays'), [])  # a recurring day names no date

    def test_next_weekday_written_on_that_weekday_never_offers_the_sent_day(self):
        self.assertEqual(said('next Monday', sent='2026-10-05T10:00:00-05:00'),
                         [('next Monday', ['2026-10-12', '2026-10-19'], 'last_next')])
        found = dates.mentions('next Monday', '2026-10-05T10:00:00-05:00')
        self.assertEqual([dates.match(d, found)['kind'] for d in ('2026-10-05', '2026-10-19')], ['missing', 'ambiguous'])

    def test_weekday_abbreviations_are_read_only_as_written_capitalized(self):
        self.assertEqual(said('Can we meet Thurs at 2? Or Tues, Thu, Thur, next Fri'), [
            ('Thurs', ['2026-10-01', '2026-10-08'], 'weekday'), ('Tues', ['2026-10-06', '2026-10-13'], 'weekday'),
            ('Thu', ['2026-10-01', '2026-10-08'], 'weekday'), ('Thur', ['2026-10-01', '2026-10-08'], 'weekday'),
            ('next Fri', ['2026-10-09', '2026-10-16'], 'last_next')])
        self.assertEqual(said('I missed class Mon and Wed.'), [('Mon', ['2026-10-05', '2026-10-12'], 'weekday'),
                                                               ('Wed', ['2026-10-07', '2026-10-14'], 'weekday')])
        self.assertEqual(said('This Sat works. Sun at 2? Wed.'), [('This Sat', ['2026-10-10'], 'definite'),
                                                                  ('Sun', ['2026-10-04', '2026-10-11'], 'weekday'),
                                                                  ('Wed', ['2026-10-07', '2026-10-14'], 'weekday')])
        self.assertEqual(said('Mon, Oct 5 and Thurs 10/8'), [('Mon, Oct 5', ['2026-10-05'], 'definite'),
                                                             ('Thurs 10/8', ['2026-10-08'], 'definite')])
        # Lower case, all capitals, inside words, and "Sat"/"Sun"/"Wed" opening a sentence of other prose are no day.
        for text in ('I sat in back and the sun was out', 'mon wed fri', 'The SAT is hard', 'Monitor the Wednesdays',
                     'Sat in the back row. Sun was out.', 'Wed in June, they moved', 'Sunny day'):
            with self.subTest(text=text):
                self.assertEqual(said(text), [])

    def test_a_span_of_days_names_each_of_its_days(self):
        five_to_seven = ['2026-10-05', '2026-10-06', '2026-10-07']
        for text in ('I will be out Oct 5-7', 'October 5th through the 7th', '10/5-10/7', '5–7 October', 'Oct 5 to Oct 7',
                     'Oct. 5 – 7, 2026', '10/5 - 7', '5th to the 7th of October'):
            with self.subTest(text=text):
                self.assertEqual([(m[1], m[2]) for m in said(text)], [(five_to_seven, 'range')])
        self.assertEqual(said('Sep 30 - Oct 2')[0][1], ['2026-09-30', '2026-10-01', '2026-10-02'])  # into the next month
        self.assertEqual(said('9/30-10/2')[0][1], ['2026-09-30', '2026-10-01', '2026-10-02'])
        self.assertEqual(said('30 Sep to 2 Oct')[0][1], ['2026-09-30', '2026-10-01', '2026-10-02'])
        self.assertEqual(said('Dec 30-Jan 2')[0][1], ['2026-12-30', '2026-12-31', '2027-01-01', '2027-01-02'])
        # No span: an end before the start, a month skipped, a time after the end, or a span over a month: read as before.
        self.assertEqual(said('Oct 7-5'), [('Oct 7', ['2026-10-07'], 'definite')])
        self.assertEqual(said('Oct 5 - 7 pm'), [('Oct 5', ['2026-10-05'], 'definite')])
        self.assertEqual(said('Oct 5 to 10:30'), [('Oct 5', ['2026-10-05'], 'definite')])
        self.assertEqual(said('Sep 5 - Nov 7'), [('Sep 5', ['2026-09-05'], 'definite'), ('Nov 7', ['2026-11-07'], 'definite')])
        self.assertEqual(said('9/1-10/30'), [('9/1', ['2026-09-01'], 'definite'), ('10/30', ['2026-10-30'], 'definite')])
        found = dates.mentions('I will be out Oct 5-7', SENT)
        self.assertEqual([dates.match(d, found)['kind'] for d in ('2026-10-05', '2026-10-07', '2026-10-08')], ['named', 'named', 'missing'])

    def test_quoted_replies_and_a_missing_or_naive_send_time_name_nothing(self):
        self.assertEqual(said('Thanks!\n> On Monday you said the quiz moved'), [])
        self.assertEqual(said('Thanks!\nOn Tue, Oct 6, 2026 at 9:00 AM Instructor wrote:\nMonday works'), [])
        self.assertEqual(said('today', sent='2026-10-07T15:00:00'), [])
        self.assertEqual(said('today', sent=''), [])

    def test_the_send_day_is_the_campus_day_and_a_year_rolls_over(self):
        self.assertEqual(said('today', sent='2026-10-08T03:00:00Z'), [('today', ['2026-10-07'], 'definite')])  # 10 pm in College Station
        self.assertEqual(said('Jan 4', sent='2026-12-20T15:00:00Z'), [('Jan 4', ['2027-01-04'], 'definite')])
        # And back: a day over 240 days ahead in the send year is last year's ("Dec 28" written Jan 5); 240 days ahead is not.
        self.assertEqual(said('Dec 28', sent='2027-01-05T15:00:00Z'), [('Dec 28', ['2026-12-28'], 'definite')])
        self.assertEqual(said('Sep 2 or Sep 3', sent='2027-01-05T15:00:00Z'),
                         [('Sep 2', ['2027-09-02'], 'definite'), ('Sep 3', ['2026-09-03'], 'definite')])

    def test_before_dawn_a_word_for_a_day_keeps_both_readings(self):
        # Measured on a real notice: "this morning", written at 1:20 am on Oct 3, was about Oct 2.
        self.assertEqual(said('I missed the quiz this morning', sent='2026-10-03T06:20:00Z'),
                         [('this morning', ['2026-10-02', '2026-10-03'], 'definite')])
        self.assertEqual(said('tomorrow', sent='2026-10-03T06:20:00Z'), [('tomorrow', ['2026-10-03', '2026-10-04'], 'definite')])
        self.assertEqual(said('Monday', sent='2026-10-06T06:20:00Z'), [('Monday', ['2026-10-05', '2026-10-12'], 'weekday')])  # unchanged

    def test_a_week_is_its_monday_to_sunday_and_names_each_of_its_days(self):
        self.assertEqual(said('I will be out this week')[0][1], [f'2026-10-{d:02d}' for d in range(5, 12)])
        self.assertEqual(said('next week')[0][1][0], '2026-10-12')
        self.assertEqual(said('last week')[0][1][-1], '2026-10-04')
        self.assertEqual(said('the rest of the week')[0][1], [f'2026-10-{d:02d}' for d in range(7, 12)])  # sent Wednesday
        self.assertEqual(said('the rest of this week', sent='2026-10-07T05:27:00Z')[0][1][0], '2026-10-06')  # before dawn: from Tuesday
        found = dates.mentions('I will miss class all of next week', SENT)
        self.assertEqual([dates.match(day, found)['kind'] for day in ('2026-10-12', '2026-10-16', '2026-10-19')], ['named', 'named', 'missing'])

    def test_times_keep_both_readings_without_am_or_pm(self):
        found = [(t['words'], t['times']) for t in dates.times('Thursday at 2? Or 2:30-3pm, or 11 am, or at 12')]
        self.assertEqual(found, [('at 2', ['02:00', '14:00']), ('2:30-3pm', ['14:30']), ('11 am', ['11:00']), ('at 12', ['12:00'])])
        self.assertEqual(dates.times('> at 2pm\nNo'), [])

    def test_a_clock_range_across_noon_starts_in_the_morning(self):
        for text, start in (('11-1pm', '11:00'), ('11:30-12:30pm', '11:30'), ('11-12pm', '11:00'), ('11 to 1 pm', '11:00'),
                            ('9-1pm', '09:00'), ('12-1pm', '12:00'), ('12-12:30pm', '12:00'), ('2-3pm', '14:00'),
                            ('2:30-3pm', '14:30'), ('10-11am', '10:00'), ('11-1am', '23:00')):
            with self.subTest(text=text):
                self.assertEqual([t['times'] for t in dates.times(text)], [[start]])
        self.assertEqual([t['times'] for t in dates.times('11am-1pm')], [['11:00'], ['13:00']])


class MatchTests(unittest.TestCase):
    def test_a_day_is_named_a_weekday_candidate_ambiguous_or_missing(self):
        found = dates.mentions('I was sick yesterday. I missed Monday, and next Monday I have a game.', SENT)
        self.assertEqual(dates.match('2026-10-06', found)['kind'], 'named')
        self.assertEqual((dates.match('2026-10-05', found)['kind'], dates.match('2026-10-05', found)['side']), ('weekday', 'past'))
        self.assertEqual(dates.match('2026-10-12', found)['side'], 'later')  # the bare weekday's coming candidate comes first
        self.assertEqual(dates.match('2026-10-19', found)['kind'], 'ambiguous')
        self.assertEqual(dates.match('2026-10-02', found)['kind'], 'missing')

    def test_a_week_stands_for_an_absence_only_when_the_absence_covers_its_class_days(self):
        # WHOLE WEEK ONLY (requirement, 2026-10-10): sent Wednesday Oct 7, "this week" is Oct 5-11 and "the rest of the week" Oct 7-11.
        def kept(text, first, last):
            found = dates.mentions(text, SENT)
            stand, aside = dates.whole_weeks(found, first, last)
            self.assertEqual(len(stand) + len(aside), len(found))
            return [m['words'] for m in stand], [m['words'] for m in aside]
        self.assertEqual(kept('I am out this week', '2026-10-05', '2026-10-09'), (['this week'], []))  # Monday to Friday
        self.assertEqual(kept('I am out this week', '2026-10-05', '2026-10-11'), (['this week'], []))
        self.assertEqual(kept('Is there a quiz this week?', '2026-10-09', '2026-10-09'), ([], ['this week']))
        self.assertEqual(kept('I have been sick this week', '2026-10-05', '2026-10-08'), ([], ['this week']))  # not its Friday
        self.assertEqual(kept('I am out the rest of the week', '2026-10-07', '2026-10-09'), (['the rest of the week'], []))
        self.assertEqual(kept('I am out the rest of the week', '2026-10-08', '2026-10-09'), ([], ['the rest of the week']))
        # Only a week is set aside: a day, a span of days or a weekday written beside it stands as before.
        self.assertEqual(kept('The quiz on this week’s material is Friday. I could also do 10/9 or Oct 8-9', '2026-10-09', '2026-10-09'),
                         (['Friday', '10/9', 'Oct 8-9'], ['this week']))
        # A week with no class day left names nothing ("the rest of the week" written on a Saturday).
        weekend = dates.mentions('I am out the rest of the week', '2026-10-10T10:00:00-05:00')
        self.assertEqual(dates.whole_weeks(weekend, '2026-10-10', '2026-10-11'), ([], weekend))

    def test_timing_counts_only_when_both_orders_agree_on_past_or_later(self):
        asked = dates.timing('In message 2', 'Monday')
        self.assertEqual(set(asked), {'timing', 'timing__reversed'})
        self.assertEqual(set(dates.timing('In the email', 'Monday', 'timing_1')), {'timing_1', 'timing_1__reversed'})
        self.assertEqual(dates.timing('In the email', 'Monday', 'timing_1')['timing_1'], asked['timing'] | {
            'instructions': asked['timing']['instructions'].replace('In message 2', 'In the email')})
        self.assertNotIn('2026', str(asked))  # no calendar date is ever put to a model

        def answers(first, second, confidence=1.0):
            return {'timing': {'choice': first, 'confidence': confidence}, 'timing__reversed': {'choice': second, 'confidence': confidence}}
        self.assertEqual(dates.timed(answers('past', 'past'), .8), 'past')
        self.assertIsNone(dates.timed(answers('past', 'later'), .8))
        self.assertIsNone(dates.timed(answers('unclear', 'unclear'), .8))
        self.assertIsNone(dates.timed(answers('later', 'later', .7), .8))
        named = {f'timing_1{suffix}': value for suffix, value in (('', answers('later', 'later')['timing']),
                                                                   ('__reversed', answers('later', 'later')['timing__reversed']))}
        self.assertEqual(dates.timed(named, name='timing_1'), 'later')


if __name__ == '__main__':
    unittest.main()
